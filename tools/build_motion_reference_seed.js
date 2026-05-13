import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..");
const SOURCE_PATH = path.join(ROOT, "eoir_page_tmp.html");
const OUTPUT_PATH = path.join(ROOT, "motion_reference_seed.py");
const EOIR_BASE = "https://www.justice.gov";

function decodeHtml(value) {
  return (value || "")
    .replace(/&nbsp;/g, " ")
    .replace(/&#8203;/g, "")
    .replace(/&amp;/g, "&")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&rsquo;/g, "'")
    .replace(/&ldquo;/g, '"')
    .replace(/&rdquo;/g, '"')
    .replace(/&ndash;/g, "-")
    .replace(/&mdash;/g, "-")
    .replace(/&uarr;/g, "")
    .replace(/&oacute;/g, "o")
    .replace(/&aacute;/g, "a")
    .replace(/&eacute;/g, "e")
    .replace(/&iacute;/g, "i")
    .replace(/&uacute;/g, "u");
}

function stripTags(value) {
  return decodeHtml(value.replace(/<[^>]*>/g, " "))
    .replace(/\s+/g, " ")
    .trim();
}

function htmlLines(value) {
  return decodeHtml(
    (value || "")
      .replace(/<br\s*\/?>/gi, "\n")
      .replace(/<\/p>/gi, "\n")
      .replace(/<\/div>/gi, "\n")
      .replace(/<\/li>/gi, "\n")
      .replace(/<[^>]*>/g, " ")
  )
    .split(/\n/)
    .map((line) => line.replace(/\s+/g, " ").trim())
    .filter(Boolean);
}

function attr(value, name) {
  const match = value.match(new RegExp(`${name}=["']([^"']+)["']`, "i"));
  return match ? decodeHtml(match[1].trim()) : "";
}

function pyString(value) {
  return JSON.stringify(value || "");
}

function cleanJudgeName(value) {
  return stripTags(value)
    .replace(/\s*\([A-Z0-9]{1,5}\)\s*$/i, "")
    .replace(/^(ACIJ|RDCIJ|DCIJ)\s+/i, "")
    .replace(/\s+/g, " ")
    .trim();
}

function parse() {
  const html = fs.readFileSync(SOURCE_PATH, "utf8");
  const courts = new Map();
  const judges = new Map();
  const courtBlockPattern = /<h3\b[^>]*>([\s\S]*?)<\/h3>\s*<table\b[^>]*>([\s\S]*?)<\/table>/gi;
  let match;

  while ((match = courtBlockPattern.exec(html))) {
    const headingHtml = match[1];
    const tableHtml = match[2];
    const name = stripTags(headingHtml);
    if (!/immigration court$/i.test(name) && !/adjudication center$/i.test(name)) {
      continue;
    }

    const linkMatch = headingHtml.match(/<a\b[^>]*href=["']([^"']+)["']/i);
    const url = linkMatch ? new URL(decodeHtml(linkMatch[1].trim()), EOIR_BASE).toString() : "";
    courts.set(name, {
      name,
      address_line1: "",
      address_line2: "",
      city: "",
      state: "",
      postal_code: "",
      url,
    });

    const rowPattern = /<tr\b[^>]*>([\s\S]*?)<\/tr>/gi;
    let rowMatch;
    while ((rowMatch = rowPattern.exec(tableHtml))) {
      const cells = [...rowMatch[1].matchAll(/<td\b[^>]*>([\s\S]*?)<\/td>/gi)];
      if (!cells.length) {
        continue;
      }
      const judgeName = cleanJudgeName(cells[0][1]);
      if (!judgeName || judgeName.toLowerCase() === "judge name") {
        continue;
      }
      const key = `${judgeName}||${name}`;
      judges.set(key, { name: judgeName, court_name: name });
    }
  }

  return {
    courts: [...courts.values()].sort((a, b) => a.name.localeCompare(b.name)),
    judges: [...judges.values()].sort((a, b) => a.name.localeCompare(b.name)),
  };
}

function parseCityStateZip(value) {
  const match = (value || "").match(/^(.*?),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)$/);
  return match ? { city: match[1], state: match[2], postal_code: match[3] } : null;
}

async function addCourtAddresses(courts) {
  for (const court of courts) {
    if (!court.url) {
      continue;
    }
    try {
      const response = await fetch(court.url, {
        headers: { "User-Agent": "Mozilla/5.0 ApexFilingSeedBuilder/1.0" },
      });
      if (!response.ok) {
        continue;
      }
      const html = await response.text();
      const addressBlockMatch = html.match(/<h[23][^>]*>\s*Address\s*<\/h[23]>([\s\S]*?)(?:<h[234]\b|<\/article>|<\/main>)/i);
      if (!addressBlockMatch) {
        continue;
      }
      const lines = htmlLines(addressBlockMatch[1]);
      const cityLineIndex = lines.findIndex((line) => parseCityStateZip(line));
      if (cityLineIndex === -1) {
        continue;
      }
      const parsed = parseCityStateZip(lines[cityLineIndex]);
      const addressLines = lines.slice(0, cityLineIndex);
      court.address_line1 = addressLines[0] || "";
      court.address_line2 = addressLines.slice(1).join(", ");
      court.city = parsed.city;
      court.state = parsed.state;
      court.postal_code = parsed.postal_code;
    } catch (error) {
      // Keep the court name even if a detail page is temporarily unavailable.
    }
  }
  return courts;
}

function writeSeed({ courts, judges }) {
  const lines = [
    "# Generated from EOIR's public court/hearing roster.",
    "# Source: https://www.justice.gov/eoir/find-immigration-court-and-access-internet-based-hearings",
    "",
    "SEEDED_COURTS = [",
  ];
  for (const court of courts) {
    lines.push(`    {"name": ${pyString(court.name)}, "address_line1": ${pyString(court.address_line1)}, "address_line2": ${pyString(court.address_line2)}, "city": ${pyString(court.city)}, "state": ${pyString(court.state)}, "postal_code": ${pyString(court.postal_code)}},`);
  }
  lines.push("]", "", "SEEDED_JUDGES = [");
  for (const judge of judges) {
    lines.push(
      `    {"name": ${pyString(judge.name)}, "court_name": ${pyString(judge.court_name)}},`
    );
  }
  lines.push("]", "");
  fs.writeFileSync(OUTPUT_PATH, lines.join("\n"), "utf8");
  console.log(`Wrote ${courts.length} courts and ${judges.length} judge/court rows to ${OUTPUT_PATH}`);
}

const parsed = parse();
parsed.courts = await addCourtAddresses(parsed.courts);
writeSeed(parsed);
