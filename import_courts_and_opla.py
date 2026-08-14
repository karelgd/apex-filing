import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from app import app, db, ImmigrationCourt, OPLAOffice

try:
    from motion_reference_seed import SEEDED_COURTS, SEEDED_JUDGES
except ImportError:
    SEEDED_COURTS = []
    SEEDED_JUDGES = []


EOIR_COURT_LIST_URL = "https://www.justice.gov/eoir/find-immigration-court-and-access-internet-based-hearings"
EOIR_BASE = "https://www.justice.gov"
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; ApexFilingImporter/1.0; "
        "+https://www.justice.gov/eoir/find-immigration-court-and-access-internet-based-hearings)"
    )
}

FALLBACK_COURTS = [
    {
        "name": "Miami Immigration Court",
        "address_line1": "333 S. Miami Avenue, Suite 700",
        "address_line2": "",
        "city": "Miami",
        "state": "FL",
        "postal_code": "33130",
    },
    {
        "name": "Orlando Immigration Court",
        "address_line1": "3535 Lawton Road, Suite 200",
        "address_line2": "",
        "city": "Orlando",
        "state": "FL",
        "postal_code": "32803",
    },
    {
        "name": "Krome North Service Processing Center",
        "address_line1": "18201 SW 12th Street",
        "address_line2": "",
        "city": "Miami",
        "state": "FL",
        "postal_code": "33194",
    },
]


def clean_text(value):
    return re.sub(r"\s+", " ", value or "").strip()


def looks_like_court_name(value):
    text = clean_text(value)
    if not text:
        return False
    lowered = text.lower()
    if lowered.startswith("find an immigration court"):
        return False
    return (
        lowered.endswith("immigration court")
        or lowered.endswith("adjudication center")
        or "immigration court" in lowered
    )


def parse_city_state_zip(value):
    match = re.match(r"^(.*?),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)$", clean_text(value))
    if not match:
        return None, None, None
    return match.group(1), match.group(2), match.group(3)


def fetch_soup(url):
    resp = requests.get(url, headers=REQUEST_HEADERS, timeout=30)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def scrape_eoir_roster():
    """
    Read EOIR's court/hearing roster directly. This page includes court names and
    judge rows, so it gives us a useful import even when individual court pages
    or TRAC are unavailable from PythonAnywhere.
    """
    print(f"Downloading EOIR court and judge roster from {EOIR_COURT_LIST_URL}...")
    soup = fetch_soup(EOIR_COURT_LIST_URL)
    courts = {}
    judges = {}

    for heading in soup.find_all(["h2", "h3", "h4"]):
        court_name = clean_text(heading.get_text(" ", strip=True).replace("\xa0", " "))
        if not looks_like_court_name(court_name):
            continue

        link = heading.find("a", href=True)
        courts[court_name] = {
            "name": court_name,
            "url": urljoin(EOIR_BASE, link["href"]) if link else None,
            "address_line1": None,
            "address_line2": None,
            "city": None,
            "state": None,
            "postal_code": None,
        }

        for sibling in heading.find_next_siblings():
            if sibling.name in ("h2", "h3"):
                break
            for raw_line in sibling.get_text("\n", strip=True).split("\n"):
                line = clean_text(raw_line.replace("\xa0", " "))
                judge_name = parse_eoir_judge_line(line)
                if judge_name:
                    judges[(judge_name, court_name)] = {
                        "name": judge_name,
                        "court_name": court_name,
                    }

    print(f"EOIR roster found {len(courts)} courts and {len(judges)} court/judge rows.")
    return list(courts.values()), list(judges.values())


def parse_eoir_judge_line(line):
    if not line:
        return None
    lowered = line.lower()
    if (
        "judge name" in lowered
        or "webex" in lowered
        or "access code" in lowered
        or "telephonic" in lowered
        or "http" in lowered
    ):
        return None
    if not re.search(r"\([A-Z0-9]{2,4}\)", line):
        return None
    name = re.sub(r"\s*\([A-Z0-9]{2,4}\)\s*$", "", line).strip()
    name = re.sub(r"^(ACIJ|RDCIJ|DCIJ)\s+", "", name).strip()
    if len(name.split()) < 2:
        return None
    return name


def add_court_address_from_detail_page(court):
    if not court.get("url"):
        return court
    try:
        csoup = fetch_soup(court["url"])
    except Exception as e:
        print(f"    !! Could not fetch address for {court['name']}: {e}")
        return court

    heading = csoup.find(
        lambda tag: tag.name in ("h2", "h3")
        and tag.get_text(strip=True).lower().startswith("address")
    )
    if not heading:
        return court

    lines = []
    for sibling in heading.find_next_siblings():
        if sibling.name in ("h2", "h3", "h4"):
            break
        for raw_line in sibling.get_text("\n", strip=True).split("\n"):
            line = clean_text(raw_line)
            if line:
                lines.append(line)

    if not lines:
        return court

    city, state, postal_code = parse_city_state_zip(lines[-1])
    if city and state and postal_code:
        court["city"] = city
        court["state"] = state
        court["postal_code"] = postal_code
        address_lines = lines[:-1]
        if address_lines:
            court["address_line1"] = address_lines[0]
        if len(address_lines) > 1:
            court["address_line2"] = ", ".join(address_lines[1:])
    else:
        court["address_line1"] = lines[0]
        if len(lines) > 1:
            court["address_line2"] = ", ".join(lines[1:])

    return court


def scrape_eoir_courts():
    """
    Scrape EOIR's 'Find an Immigration Court' page, then visit each court page
    and pull the street address + city/state/zip.

    Returns a list of dicts:
        {
          "name": "... Immigration Court",
          "address_line1": "...",
          "address_line2": "... or None",
          "city": "...",
          "state": "FL",
          "zip_code": "33130"
        }
    """
    try:
        roster_courts, _ = scrape_eoir_roster()
    except Exception as e:
        print(f"Could not download EOIR roster: {e}")
        if SEEDED_COURTS:
            print(f"Using bundled EOIR seed courts: {len(SEEDED_COURTS)}")
            return SEEDED_COURTS
        print("Using bundled fallback courts.")
        return FALLBACK_COURTS

    if roster_courts:
        print(f"Found {len(roster_courts)} courts on the EOIR roster. Fetching address pages when available...")
        return [add_court_address_from_detail_page(court) for court in roster_courts]
    if SEEDED_COURTS:
        print(f"EOIR roster returned no courts. Using bundled EOIR seed courts: {len(SEEDED_COURTS)}")
        return SEEDED_COURTS

    print("Downloading court list from EOIR...")
    soup = fetch_soup(EOIR_COURT_LIST_URL)

    court_links = {}

    # Heuristic: links to court pages have "immigration-court" in the URL
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)
        if not text:
            continue
        if "immigration-court" in href and ("/eoir/" in href or href.startswith("/")):
            full = urljoin(EOIR_BASE, href)
            court_links[text] = full

    if not court_links:
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(strip=True)
            if text and "immigration court" in text.lower():
                court_links[text] = urljoin(EOIR_BASE, href)

    print(f"Found {len(court_links)} possible courts. Fetching each court page...")

    courts_data = []

    for name, url in sorted(court_links.items()):
        print(f"  → {name}  ({url})")
        try:
            cr = requests.get(url, headers=REQUEST_HEADERS, timeout=30)
            cr.raise_for_status()
        except Exception as e:
            print(f"    !! Error fetching {url}: {e}")
            continue

        csoup = BeautifulSoup(cr.text, "html.parser")

        # Find the "Address" heading on the page
        heading = csoup.find(
            lambda tag: tag.name in ("h2", "h3")
            and tag.get_text(strip=True).lower().startswith("address")
        )

        address_line1 = None
        address_line2 = None
        city = None
        state = None
        zip_code = None

        if heading:
            lines = []
            for sib in heading.find_next_siblings():
                # Stop at the next heading section (Hours, Parking, etc.)
                if sib.name in ("h2", "h3", "h4"):
                    break
                txt = sib.get_text("\n", strip=True)
                for line in txt.split("\n"):
                    line = line.strip()
                    if line:
                        lines.append(line)

            # Typical pattern:
            #   One Riverview Square
            #   333 S. Miami Avenue, Suite 700
            #   Miami, FL 33130
            if lines:
                last = lines[-1]
                m = re.match(r"^(.*?),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)$", last)
                if m:
                    city = m.group(1)
                    state = m.group(2)
                    zip_code = m.group(3)

                    # Address lines are everything before the final city/state/zip line
                    if len(lines) == 2:
                        address_line1 = lines[0]
                    elif len(lines) >= 3:
                        address_line1 = lines[0]
                        address_line2 = lines[1]
                else:
                    # Fallback if regex fails – still store something
                    if len(lines) >= 1:
                        address_line1 = lines[0]
                    if len(lines) >= 2:
                        address_line2 = lines[1]

        courts_data.append(
            {
                "name": name,
                "address_line1": address_line1,
                "address_line2": address_line2,
                "city": city,
                "state": state,
                "postal_code": zip_code,
            }
        )

    print("Finished scraping EOIR courts.")
    if not courts_data:
        print("No courts scraped from EOIR page. Using bundled fallback courts.")
        return FALLBACK_COURTS
    return courts_data


def import_courts():
    """
    Use scraped EOIR data to upsert rows in ImmigrationCourt.
    Assumes ImmigrationCourt has fields:
        name, address_line1, address_line2, city, state, zip_code
    """
    with app.app_context():
        data = scrape_eoir_courts()
        created = 0
        updated = 0

        for c in data:
            if not c["name"]:
                continue

            existing = ImmigrationCourt.query.filter_by(name=c["name"]).first()

            if existing:
                if c["address_line1"]:
                    existing.address_line1 = c["address_line1"]
                if c["address_line2"]:
                    existing.address_line2 = c["address_line2"]
                if c["city"]:
                    existing.city = c["city"]
                if c["state"]:
                    existing.state = c["state"]
                if c["postal_code"]:
                    existing.zip_code = c["postal_code"]
                    existing.postal_code = c["postal_code"]
                updated += 1
            else:
                court = ImmigrationCourt(
                    name=c["name"],
                    address_line1=c["address_line1"],
                    address_line2=c["address_line2"],
                    city=c["city"],
                    state=c["state"],
                    postal_code=c["postal_code"],
                )
                db.session.add(court)
                created += 1

        db.session.commit()
        print(f"Courts imported. Created: {created}, Updated: {updated}")


# ---------- OPLA helper (manual list for now) ----------

# ============================================
# OPLA import based on your ICE Word document
# ============================================

# 1) Paste the FULL TEXT of your OPLA Word doc here.
#    Example: start at "Adelanto - OPLA" and end at the last office (e.g. "York - OPLA ...").
#    Keep the structure (blank lines, etc.) as in the document.

OPLA_TEXT = """
•	Adelanto - OPLA
Office of the Principal Legal Advisor, Los Angeles (Adelanto)
10250 Rancho Road
Adelanto, CA 92301

(760) 561-6460
•	Annandale - OPLA
Office of the Principal Legal Advisor, Washington, D.C. (Annandale)
7619 Little River Turnpike
Suite 900
Annandale, VA 22003

(703) 962-2800
Mailing Address:
500 12th Street SW, Mail Stop 5902
Washington, D.C. 20536-5902
Area of Responsibility: District of Columbia and Virginia (Annandale Immigration Court)
•	Atlanta - OPLA
Office of the Principal Legal Advisor, Atlanta
180 Ted Turner Drive, SW, Suite 332
Atlanta, GA 30303

(404) 893-1400
Area of Responsibility: Georgia, North Carolina, and South Carolina
•	Atlanta - OPLA
Office of the Principal Legal Advisor, Atlanta
Peachtree Summit Federal Building
401 W. Peachtree Street, NW, Suite 2850
Atlanta, GA 30308

(404) 730-9756
•	Baltimore - OPLA
Office of the Principal Legal Advisor, Baltimore
31 Hopkins Plaza, Room 1600
Baltimore, MD 21201

(443) 560-0600
Area of Responsibility: Maryland
•	Batavia - OPLA
Office of the Principal Legal Advisor, Buffalo (Batavia)
Buffalo Federal Detention Facility
4250 Federal Drive
Batavia, NY 14020

(585) 344-6600
•	Boston - OPLA
Office of the Principal Legal Advisor, Boston
15 New Sudbury Street, Room 425
Boston, MA 02203

(857) 416-3701
Area of Responsibility: Connecticut, Maine, Massachusetts, New Hampshire, Rhode Island, and Vermont
•	Brooklyn Heights - OPLA
Office of the Principal Legal Advisor, Detroit (Cleveland)
925 Keynote Circle, Room 201
Brooklyn Heights, OH 44131

(216) 749-9955
•	Buffalo - OPLA
Office of the Principal Legal Advisor, Buffalo
250 Delaware Avenue, Suite 773
Buffalo, NY 14202

(716) 464-6000
Area of Responsibility: Northern and Western New York
•	Centennial - OPLA
Office of the Principal Legal Advisor, Denver
12445 East Caley Avenue
Centennial, CO 80111-6432

(983) 212-0405
Area of Responsibility: Colorado, Idaho (ERO) , Montana, Utah, and Wyoming
•	Chaparral - OPLA
Office of the Principal Legal Advisor, El Paso (Chaparral)
Trial Attorney Unit
26 McGregor Range Road
Chaparral, NM 88081

(915) 834-5200
•	Charlotte - OPLA
Office of the Principal Legal Advisor, Atlanta (Charlotte)
5701 Executive Center Drive, Suite 300
Charlotte, NC 28212

(704) 248-9605
•	Chicago - OPLA
Office of the Principal Legal Advisor, Chicago
55 E. Monroe Street
Suite 1400
Chicago, IL 60603

(312) 260-9513
Area of Responsibility: Illinois, Indiana, Kansas, Kentucky, Missouri, and Wisconsin
•	Conroe - OPLA
Office of the Principal Legal Advisor, Houston (Conroe)
Montgomery Processing Center
806 Hilbig Road
Suite 2-201
Conroe, TX 77301

(936) 520-5870
•	Detroit - OPLA
Office of the Principal Legal Advisor, Detroit
Rosa Parks Federal Building
985 Michigan Avenue, Suite 1010
Detroit, MI 48226

(313) 771-6500
Area of Responsibility: Michigan and Ohio
•	Dilley - OPLA
Office of the Principal Legal Advisor, San Antonio (Dilley)
South Texas Family Residential Center
300 El Rancho Way
Dilley, TX 78017

(830) 378-6500
•	El Paso - OPLA
Office of the Principal Legal Advisor, El Paso
11541 Montana Avenue, Suite O
El Paso, TX 79936

(915) 856-2316
Area of Responsibility: West Texas and New Mexico
•	Elizabeth - OPLA
Office of the Principal Legal Advisor, Newark (Elizabeth)
Elizabeth Detention Facility
625 Evans Street, Room 135
Elizabeth, NJ 07201

(908) 282-5755
•	Eloy - OPLA
Office of the Principal Legal Advisor, Phoenix (Eloy)
Eloy Detention Center
1705 East Hanna Road
Eloy, AZ 85131

(520) 464-3032
•	Florence - OPLA
Office of the Principal Legal Advisor, Phoenix (Florence)
Florence Detention Center
3250 N. Pinal Parkway Avenue
Florence, AZ 85132

(520) 868-3310

•	Fort Snelling - OPLA
Office of the Principal Legal Advisor, Minneapolis-St. Paul
1 Federal Drive, Suite 1800
Fort Snelling, MN 55111

(612) 843-8935
Area of Responsibility: Iowa, Minnesota, Nebraska, North Dakota and South Dakota
•	Guaynabo - OPLA
Office of the Principal Legal Advisor, Miami (San Juan)
7 Tabonuco Street
Room 300 (Suite 313)
Guaynabo, PR 00968

(787) 706-2352
•	Harlingen - OPLA
Office of the Principal Legal Advisor, San Antonio (Harlingen)
1717 Zoy Street, Annex
Harlingen, TX 78552

(956) 389-7051
•	Hartford - OPLA
Office of the Principal Legal Advisor, Boston (Hartford)
Ribicoff Federal Building
450 Main Street, Room 483
Hartford, CT 06103-3060

(860) 240-3615
•	Honolulu - OPLA
Office of the Principal Legal Advisor, Honolulu
300 Ala Moana Boulevard
Suite 7-220
Honolulu, HI 96850

(808) 529-1900
Area of Responsibility: Hawaii, Northern Mariana Islands, Guam, Saipan
•	Houston - OPLA
Office of the Principal Legal Advisor, Houston
126 Northpoint Drive, Room 2020
Houston, TX 77060

(281) 931-2046
Area of Responsibility: Southeast Texas
•	Hyattsville - OPLA
Office of the Principal Legal Advisor, Hyattsville
6505 Belcrest Road
Suite 450
Hyattsville, MD 20782

(443) 560-0600
Mailing address:
500 12th Street, S.W., Stop 5904
Washington, DC 20536-5904
•	Imperial - OPLA
Office of the Principal Legal Advisor, Imperial
2409 La Brucherie Road
Suite 3
Imperial, CA 92251

(760) 355-8361
•	Indianapolis - OPLA
Office of The Principal Legal Advisor, Chicago (Indianapolis)
575 N. Pennsylvania Street
Suite 646
Indianapolis, IN 46204

(312) 260-9513
•	Irving - OPLA
Office of the Principal Legal Advisor, Dallas
125 E. John Carpenter Fwy., Suite 500
Irving, TX 75062

(972) 373-2300
Area of Responsibility: North Texas and Oklahoma
•	Jena - OPLA
Office of the Principal Legal Advisor, New Orleans (Jena)
LaSalle Detention Center
830 Pinehill Road
Jena, LA 71342

(318) 992-1455
•	Kansas City - OPLA
Office of the Principal Legal Advisor, Chicago (Kansas City)
2345 Grand Boulevard, Suite 500
Kansas City, MO 64108

(816) 391-7200
•	Las Vegas - OPLA
Office of the Principal Legal Advisor, Los Angeles (Las Vegas)
501 S. Las Vegas Blvd, Suite 200
Las Vegas, NV 89101

(702) 433-7288
•	Los Angeles - OPLA
Office of the Principal Legal Advisor, Los Angeles
606 South Olive Street, 8th Floor
Los Angeles, CA 90014

(213) 894-2805
Area of Responsibility: Greater Los Angeles Metropolitan Area and Nevada
•	Los Angeles - OPLA
Office of the Principal Legal Advisor, Los Angeles (North Los Angeles)
300 N. Los Angeles Street
Suite 1240
Los Angeles, CA 90012

(213) 830-5555
•	Los Fresnos - OPLA
Office of the Principal Legal Advisor, San Antonio (Los Fresnos)
Port Isabel Detention Center
27991 Buena Vista Blvd
Los Fresnos, TX 78566

(956) 547-1700
•	Louisville - OPLA
Office of the Principal Legal Advisor, Chicago (Louisville)
55 E. Monroe Street
Suite 1400
Chicago, IL 60603

•	Illinois - OPLA
Office of the Principal Legal Advisor, Chicago (Illinois)
55 E. Monroe Street
Suite 1400
Chicago, IL 60603

•	Indiana - OPLA
Office of the Principal Legal Advisor, Chicago (Indiana)
55 E. Monroe Street
Suite 1400
Chicago, IL 60603

•	Kansas - OPLA
Office of the Principal Legal Advisor, Chicago (Kansas)
55 E. Monroe Street
Suite 1400
Chicago, IL 60603

•	Kentucky - OPLA
Office of the Principal Legal Advisor, Chicago (Kentucky)
55 E. Monroe Street
Suite 1400
Chicago, IL 60603

•	Missouri - OPLA
Office of the Principal Legal Advisor, Chicago (Missouri)
55 E. Monroe Street
Suite 1400
Chicago, IL 60603

•	Wisconsin - OPLA
Office of the Principal Legal Advisor, Chicago (Wisconsin)
55 E. Monroe Street
Suite 1400
Chicago, IL 60603

•	Lumpkin - OPLA
Office of the Principal Legal Advisor, Atlanta (Lumpkin)
Stewart County Detention Facility
146 CCA Road
Lumpkin, GA 31815

(229) 838-1109
•	Memphis - OPLA
Office of the Principal Legal Advisor, New Orleans (Memphis)
80 Monroe Avenue, Suite 200
Memphis, TN 38103

(901) 462-9410
•	Miami - OPLA
Office of the Principal Legal Advisor, Miami
Krome Service Processing Center
18201 SW 12th Street
Miami, FL 33194-2700

(305) 207-2001
•	Miami - OPLA
Office of the Principal Legal Advisor, Miami
333 S. Miami Avenue, Suite 200
Miami, FL 33130

(305) 400-6160
Area of Responsibility: South Florida, Puerto Rico, and the Virgin Islands

•	New Orleans - OPLA
Office of the Principal Legal Advisor, New Orleans
423 Canal Street
Suite 450
New Orleans, LA 70130

(504) 514-0001
Area of Responsibility: Alabama, Arkansas, Kentucky, Louisiana, Mississippi, and Tennessee
•	New York - OPLA
Office of the Principal Legal Advisor, New York (Varick Street)
201 Varick Street, Room 738
New York, NY 10014

(212) 367-6300
•	New York - OPLA
Office of the Principal Legal Advisor, New York
26 Federal Plaza, Room 1130
New York, NY 10278

(212) 436-9100
Area of Responsibility: Long Island, New York City, and Southern Counties
•	Newark - OPLA
Office of the Principal Legal Advisor, Newark
970 Broad Street, Room 1300
Newark, NJ 07102

(973) 776-5400
Area of Responsibility: New Jersey
•	Newburgh - OPLA
Office of the Principal Legal Advisor, New York (Newburgh)
Hudson Valley
15 Governor Drive
Newburgh, NY 12550

(845) 831-1576
•	Oakdale - OPLA
Office of the Principal Legal Advisor, New Orleans (Oakdale)
1010 E. Whatley Road
Oakdale, LA 71463-1128

(318) 335-7500
•	Omaha - OPLA
Office of the Principal Legal Advisor, Minneapolis-St. Paul (Omaha)
1717 Avenue H, Room 174
Omaha, NE 68110

(402) 536-4804
•	Orlando - OPLA
Office of the Principal Legal Advisor, Orlando
500 North Orange Avenue
Suite 5000
Orlando, FL 32801

(689) 319-0900
Area of Responsibility: Central and North Florida
•	Pearsall - OPLA
Office of the Principal Legal Advisor, San Antonio (Pearsall)
South Texas ICE Processing Center
566 Veterans Drive
Pearsall, TX 78061

(210) 231-4630
•	Philadelphia - OPLA
Office of the Principal Legal Advisor, Philadelphia
900 Market Street, Suite 346
Philadelphia, PA 19107

(267) 479-3500
Area of Responsibility: Delaware, Pennsylvania, and West Virginia
•	Phoenix - OPLA
Office of the Principal Legal Advisor, Phoenix
2035 N. Central Avenue
Phoenix, AZ 85004

(602) 744-2412
Area of Responsibility: Arizona
•	Pompano Beach - OPLA
Office of the Principal Legal Advisor, Miami (Pompano Beach)
Broward Transitional Center
3900 North Powerline Road
Pompano Beach, FL 33073

(954) 545-6060
•	Portland - OPLA
Office of the Principal Legal Advisor, Seattle (Portland)
1220 SW 3rd Avenue, Suite 300
Portland, OR 97204

(503) 326-2059
•	Sacramento - OPLA
Office of the Principal Legal Advisor, San Francisco (Sacramento)
Sacramento, CA 95814


Mailing Address:
100 Montgomery Street
Suite 200
San Francisco, CA 94104
•	San Antonio - OPLA
Office of the Principal Legal Advisor, San Antonio
1015 Jackson-Keller Road, Suite 100
San Antonio, TX 78213

(210) 979-4600
Area of Responsibility: Central and South Texas
•	San Diego - OPLA
Office of the Principal Legal Advisor, San Diego (Otay Mesa)
7488 Calzada de la Fuente
San Diego, CA 92154

(619) 661-3940
Mailing Address:
P.O. Box 438150
San Diego, CA 92143-8150
•	San Diego - OPLA
Office of the Principal Legal Advisor, San Diego
880 Front Street, Suite 2246
San Diego, CA 92101

(619) 436-0277
Area of Responsibility: San Diego and Imperial County
•	San Francisco - OPLA
Office of the Principal Legal Advisor, San Francisco - Detained
630 Sansome Street,
11th Floor
San Francisco, CA 94111

(415) 705-1855
•	San Francisco - OPLA
Office of the Principal Legal Advisor, San Francisco
100 Montgomery Street, Suite 200
San Francisco, CA 94104

(415) 705-4604
Area of Responsibility: Northern and Central California
•	Santa Ana - OPLA
Office of the Principal Legal Advisor, Los Angeles (Santa Ana)
1251 E. Dyer Road
Suite 200
Santa Ana, CA 92705
•	Seattle - OPLA
Office of the Principal Legal Advisor, Seattle
915 Second Avenue
Suite 708
Seattle, WA 98174

(206) 802-7666
Area of Responsibility: Alaska, Idaho (HSI) , Oregon, and Washington
•	Sterling - OPLA
Office of the Principal Legal Advisor, Washington, D.C. (Sterling)
21400 Ridgetop Circle
Suite 100
Sterling, VA 20166

(571) 262-2300
Mailing Address:
500 12th Street SW, Mail Stop 5906
Washington, D.C. 20536-5906
Area of Responsibility: District of Columbia, West Virginia, and Virginia (Sterling Immigration Court)
•	Tacoma - OPLA
Office of the Principal Legal Advisor, Seattle (Tacoma)
Northwest ICE Processing Center
1623 East J Street, Suite 2
Tacoma, WA 98421

(253) 779-6059
•	Tucson - OPLA
Office of the Principal Legal Advisor, Phoenix (Tucson)
6431 S. Country Club Road
Tucson, AZ 85706

(520) 295-4167
•	Van Nuys - OPLA
Office of the Principal Legal Advisor, Los Angeles (Van Nuys)
6230 Van Nuys Boulevard
Suite 1011
Van Nuys, CA 91401

(818) 670-3000
•	West Valley City - OPLA
Office of the Principal Legal Advisor, Denver (Salt Lake City)
2975 Decker Lake Drive, Stop C
West Valley City, UT 84119-6098

(801) 736-1340
•	York - OPLA
Office of the Principal Legal Advisor, Philadelphia (York)
2350 Freedom Way
Suite 254
York, PA 17402

(717) 747-7250



"""


def parse_opla_text(raw_text: str):
    """
    Parses the OPLA_TEXT content into a list of dicts:
      {
        "name": "OPLA Miami (Pompano Beach)",
        "address_line1": "3900 North Powerline Road",
        "address_line2": "",
        "city": "Pompano Beach",
        "state": "FL",
        "postal_code": "33073",
      }

    Assumes the structure from your doc:
      <Location> - OPLA
      (blank)
      Office of the Principal Legal Advisor, ...
      <address line 1>
      [<address line 2>]
      <City, ST ZIP>
      (phone)
      [Area of Responsibility / Mailing Address ...]
    """
    import re

    lines = [l.rstrip() for l in raw_text.splitlines()]
    entries = []

    i = 0
    n = len(lines)

    while i < n:
        line = lines[i].strip()
        # Header line that starts a section, e.g. "Miami - OPLA"
        if line.endswith("- OPLA"):
            section = []
            i += 1
            while i < n and not lines[i].strip().endswith("- OPLA"):
                section.append(lines[i].rstrip())
                i += 1

            # Find the "Office of the Principal Legal Advisor, ..." line
            title_idx = None
            for idx, l in enumerate(section):
                if l.strip().startswith("Office of"):
                    title_idx = idx
                    break
            if title_idx is None:
                continue

            office_title = section[title_idx].strip()

            # Collect address lines after that title
            addr_lines = []
            for l in section[title_idx + 1 :]:
                s = l.strip()
                if not s:
                    # stop if we've already collected some address lines
                    if addr_lines:
                        break
                    else:
                        continue
                if s.startswith("("):  # phone
                    break
                if s.startswith("Area of Responsibility"):
                    break
                if s.startswith("Mailing Address"):
                    break
                addr_lines.append(s)

            if not addr_lines:
                continue

            # Identify the "City, ST ZIP" line (usually the last address line)
            city = state = postal_code = ""
            city_idx = None

            for idx in range(len(addr_lines) - 1, -1, -1):
                s = addr_lines[idx]
                m = re.search(r",\s*([A-Z]{2})\s+([\d\-]{5,})$", s)
                if m:
                    city_idx = idx
                    city = s[: s.rfind(",")].strip()
                    state = m.group(1)
                    postal_code = m.group(2)
                    break

            if city_idx is None:
                # fallback: if we didn't detect explicit pattern, just use last line as city line
                s = addr_lines[-1]
                m = re.search(r",\s*([A-Z]{2})\s+([\d\-]{5,})$", s)
                if m:
                    city_idx = len(addr_lines) - 1
                    city = s[: s.rfind(",")].strip()
                    state = m.group(1)
                    postal_code = m.group(2)

            # Address lines before the city line become address_line1, address_line2
            address_line1 = ""
            address_line2 = ""

            if city_idx is not None:
                if city_idx >= 1:
                    address_line1 = addr_lines[0]
                if city_idx >= 2:
                    address_line2 = addr_lines[1]
            else:
                # If we never identified a city line, at least capture the first line
                if addr_lines:
                    address_line1 = addr_lines[0]

            # Construct a nice name, e.g. "OPLA Miami (Pompano Beach)"
            name = office_title
            name = name.replace("Office of the Principal Legal Advisor, ", "OPLA ")
            name = name.replace("Office of The Principal Legal Advisor, ", "OPLA ")

            entries.append(
                {
                    "name": name,
                    "address_line1": address_line1,
                    "address_line2": address_line2,
                    "city": city,
                    "state": state,
                    "postal_code": postal_code,
                }
            )

        else:
            i += 1

    return entries

def import_opla():
    """
    Create/update OPLAOffice records based on the OPLA_TEXT above.
    """
    from app import OPLAOffice  # avoid circular import

    with app.app_context():
        data = parse_opla_text(OPLA_TEXT)

        created = 0
        updated = 0

        for o in data:
            if not o["name"]:
                continue

            existing = OPLAOffice.query.filter_by(name=o["name"]).first()
            if existing:
                existing.address_line1 = o["address_line1"]
                existing.address_line2 = o["address_line2"]
                existing.city = o["city"]
                existing.state = o["state"]
                existing.postal_code = o["postal_code"]
                updated += 1
            else:
                office = OPLAOffice(
                    name=o["name"],
                    address_line1=o["address_line1"],
                    address_line2=o["address_line2"],
                    city=o["city"],
                    state=o["state"],
                    postal_code=o["postal_code"],
                )
                db.session.add(office)
                created += 1

        db.session.commit()
        print(f"OPLA offices imported. Created: {created}, Updated: {updated}")

# ============================================
# JUDGES import from TRAC Judge Reports page
# ============================================

TRAC_JUDGES_URL = "https://tracreports.org/immigration/reports/judgereports/"


def scrape_trac_judge_names():
    """
    Descarga la tabla de TRAC y extrae SOLO los nombres de jueces
    (texto de los enlaces tipo 'Apellidos, Nombre').
    Devuelve una lista de nombres únicos.
    """
    print(f"Downloading judge list from {TRAC_JUDGES_URL}...")
    resp = requests.get(TRAC_JUDGES_URL, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    names = set()

    # Regla simple: todos los <a> cuyo texto contiene coma y parece "Apellido, Nombre"
    for a in soup.find_all("a"):
        text = a.get_text(strip=True)
        if not text:
            continue
        # nos quedamos con cosas tipo "Riley, Kevin W."
        if "," in text and " " in text:
            # filtro rápido para evitar "Denial Rate Tables", etc.
            if "Denial" in text or "Tables" in text:
                continue
            names.add(text)

    names_list = sorted(names)
    print(f"Found {len(names_list)} distinct judge names.")
    return names_list


def scrape_eoir_judges():
    try:
        _, judges = scrape_eoir_roster()
    except Exception as e:
        print(f"Could not download EOIR judge roster: {e}")
        return []
    return judges


def import_judges():
    """
    Importa nombres de jueces desde TRAC y los guarda en la tabla Judge.

    Detecta automáticamente qué columna de la tabla Judge usar para el nombre:
    toma la primera columna que NO sea primary key (normalmente el nombre).
    """
    from app import Judge  # evitar import circular

    with app.app_context():
        # Detectar la columna de "nombre" automáticamente
        name_col = Judge.__table__.columns.get("name")
        if name_col is None:
            non_pk_cols = [c for c in Judge.__table__.columns if not c.primary_key]
            if not non_pk_cols:
                raise RuntimeError("No non-primary-key columns found on Judge table.")
            name_col = non_pk_cols[0]
        name_field = name_col.name

        print(f"Using Judge.{name_field} as the name field.")

        judges = scrape_eoir_judges()
        if judges:
            print(f"Using {len(judges)} judges from EOIR court/hearing roster.")
        elif SEEDED_JUDGES:
            judges = SEEDED_JUDGES
            print(f"Using bundled EOIR seed judges: {len(judges)}")
        else:
            print("EOIR judge roster returned no rows. Trying TRAC judge report as fallback...")
            try:
                judges = [{"name": name, "court_name": None} for name in scrape_trac_judge_names()]
            except Exception as e:
                print(f"Could not download TRAC judge report: {e}")
                judges = []

        created = 0
        updated = 0
        unchanged = 0

        for judge_data in judges:
            full_name = judge_data["name"]
            court_name = judge_data.get("court_name")
            # Buscar por esa columna
            existing = Judge.query.filter(name_col == full_name).first()
            if existing:
                if hasattr(existing, "court_name") and court_name and existing.court_name != court_name:
                    existing.court_name = court_name
                    updated += 1
                else:
                    unchanged += 1
                continue

            # Crear instancia y asignar el valor a la columna detectada
            judge = Judge()
            setattr(judge, name_field, full_name)
            if hasattr(judge, "court_name"):
                judge.court_name = court_name
            db.session.add(judge)
            created += 1

        db.session.commit()
        print(f"Judges imported. Created: {created}, Updated: {updated}, Existing unchanged: {unchanged}")




if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python import_courts_and_opla.py [courts|opla|judges]")
        sys.exit(1)

    cmd = sys.argv[1].lower()

    if cmd == "courts":
        import_courts()
    elif cmd == "opla":
        import_opla()
    elif cmd == "judges":
        import_judges()
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
