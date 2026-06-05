import argparse
import os
import re
import sqlite3
from datetime import datetime


TARGET_AGENCY_NAME = "Odaisa Consultancy Services LLC"

FIRST_NAME_KEYS = ("first_name", "firstname", "first", "nombre")
MIDDLE_NAME_KEYS = ("middle_name", "middlename", "middle", "segundo")
LAST_NAME_KEYS = ("last_name", "lastname", "last", "apellido")
FULL_NAME_KEYS = ("full_name", "fullname", "name", "client_name", "nombre_completo")
A_NUMBER_KEYS = (
    "a_number",
    "alien_number",
    "anumber",
    "a_num",
    "alien",
    "alien_no",
    "alien_number_if_any",
    "alien_registration_number",
    "alien_registration_no",
    "alien_reg_number",
    "alien_reg_no",
    "registration_number",
    "numero_alien",
    "numero_a",
    "imported_a_number",
)
PHONE_KEYS = ("phone", "phone_number", "mobile", "cell", "telephone", "telefono")
EMAIL_KEYS = ("email", "email_address", "correo")
STREET_KEYS = ("street_address", "address", "direccion", "home_address")
APT_KEYS = ("apartment", "apt", "unit", "suite")
CITY_KEYS = ("city", "ciudad")
STATE_KEYS = ("state", "estado")
ZIP_KEYS = ("zip_code", "zipcode", "zip", "postal_code")


def normalize_key(value):
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def first_present(row, keys):
    normalized = {normalize_key(key): key for key in row.keys()}
    for key in keys:
        actual_key = normalized.get(normalize_key(key))
        if actual_key:
            value = row[actual_key]
            if value is not None and str(value).strip():
                return str(value).strip()
    return ""


def split_name(full_name):
    parts = [part for part in re.split(r"\s+", (full_name or "").strip()) if part]
    if not parts:
        return "", "", ""
    if len(parts) == 1:
        return parts[0], "", "Unknown"
    if len(parts) == 2:
        return parts[0], "", parts[1]
    return parts[0], " ".join(parts[1:-1]), parts[-1]


def compact_phone(value):
    return re.sub(r"\s+", " ", (value or "").strip())


def make_username(first_name, last_name, a_number, fallback_number):
    base = f"{first_name}.{last_name}".lower()
    base = re.sub(r"[^a-z0-9]+", ".", base).strip(".") or "client"
    suffix = re.sub(r"[^a-z0-9]", "", (a_number or "").lower())[-6:] or str(fallback_number)
    return f"imported.{base}.{suffix}"[:80]


def unique_username(username, Client):
    candidate = username[:80]
    counter = 2
    while Client.query.filter_by(username=candidate).first():
        suffix = f".{counter}"
        candidate = f"{username[:80 - len(suffix)]}{suffix}"
        counter += 1
    return candidate


def likely_client_table(table_name, columns):
    lowered_table = table_name.lower()
    lowered_columns = {column.lower() for column in columns}
    has_name = any(normalize_key(column) in {normalize_key(key) for key in FIRST_NAME_KEYS + FULL_NAME_KEYS} for column in columns)
    has_contact = any(normalize_key(column) in {normalize_key(key) for key in PHONE_KEYS + EMAIL_KEYS + A_NUMBER_KEYS} for column in columns)
    if "client" in lowered_table and has_contact:
        return True
    return has_name and has_contact and not any(token in lowered_table for token in ("case", "invoice", "payment", "appointment", "document"))


def discover_client_rows(old_db_path, table_name=None):
    connection = sqlite3.connect(old_db_path)
    connection.row_factory = sqlite3.Row
    try:
        tables = [
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        candidates = []
        for table in tables:
            columns = [row["name"] for row in connection.execute(f'PRAGMA table_info("{table}")')]
            if table_name and table != table_name:
                continue
            if table_name or likely_client_table(table, columns):
                count = connection.execute(f'SELECT COUNT(*) AS count FROM "{table}"').fetchone()["count"]
                candidates.append((table, columns, count))
        if not candidates:
            return [], []
        candidates.sort(key=lambda item: (0 if "client" in item[0].lower() else 1, -item[2], item[0]))
        table, columns, _count = candidates[0]
        tables_lower = {name.lower() for name in tables}
        columns_lower = {name.lower() for name in columns}
        if table.lower() == "client" and "alien_number" in tables_lower and "id" in columns_lower:
            rows = [
                dict(row)
                for row in connection.execute(
                    'SELECT client.*, alien_number.value AS imported_a_number '
                    'FROM client LEFT JOIN alien_number ON alien_number.client_id = client.id'
                )
            ]
        else:
            rows = [dict(row) for row in connection.execute(f'SELECT * FROM "{table}"')]
        return rows, [(name, count) for name, _columns, count in candidates]
    finally:
        connection.close()


def row_to_client_data(row, index, a_number_column=None):
    first_name = first_present(row, FIRST_NAME_KEYS)
    middle_name = first_present(row, MIDDLE_NAME_KEYS)
    last_name = first_present(row, LAST_NAME_KEYS)
    if not first_name or not last_name:
        split_first, split_middle, split_last = split_name(first_present(row, FULL_NAME_KEYS))
        first_name = first_name or split_first
        middle_name = middle_name or split_middle
        last_name = last_name or split_last
    return {
        "first_name": first_name or "Unknown",
        "middle_name": middle_name,
        "last_name": last_name or "Unknown",
        "a_number": (str(row.get(a_number_column) or "").strip() if a_number_column else first_present(row, A_NUMBER_KEYS)),
        "phone": compact_phone(first_present(row, PHONE_KEYS)) or "000-000-0000",
        "email": first_present(row, EMAIL_KEYS) or f"imported.client.{index}@example.invalid",
        "street_address": first_present(row, STREET_KEYS) or "Address not provided",
        "apartment": first_present(row, APT_KEYS),
        "city": first_present(row, CITY_KEYS) or "Unknown",
        "state": (first_present(row, STATE_KEYS) or "NA")[:2].upper(),
        "zip_code": first_present(row, ZIP_KEYS) or "00000",
    }


def client_exists(agency_id, data, Client):
    filters = [Client.agency_id == agency_id]
    if data["a_number"]:
        existing = Client.query.filter(*filters, Client.a_number == data["a_number"]).first()
        if existing:
            return existing
    if data["email"] and not data["email"].endswith("@example.invalid"):
        existing = Client.query.filter(*filters, Client.email == data["email"]).first()
        if existing:
            return existing
    return Client.query.filter(
        Client.agency_id == agency_id,
        Client.first_name == data["first_name"],
        Client.last_name == data["last_name"],
        Client.phone == data["phone"],
    ).first()


def inspect_old_database(old_db_path, table_name=None, sample=3):
    connection = sqlite3.connect(old_db_path)
    connection.row_factory = sqlite3.Row
    try:
        tables = [
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        for table in tables:
            if table_name and table != table_name:
                continue
            columns = [row["name"] for row in connection.execute(f'PRAGMA table_info("{table}")')]
            count = connection.execute(f'SELECT COUNT(*) AS count FROM "{table}"').fetchone()["count"]
            print(f"\nTable: {table} ({count} rows)")
            print("Columns:")
            for column in columns:
                print(f"  - {column}")
            sample_rows = [dict(row) for row in connection.execute(f'SELECT * FROM "{table}" LIMIT ?', (sample,))]
            if sample_rows:
                print("Sample rows:")
                for index, row in enumerate(sample_rows, start=1):
                    preview = []
                    for column in columns:
                        value = row.get(column)
                        if value is not None and str(value).strip():
                            preview.append(f"{column}={str(value).strip()[:60]}")
                    print(f"  {index}. {' | '.join(preview[:12])}")
    finally:
        connection.close()


def import_clients(old_db_path, commit=False, table_name=None, limit=None, a_number_column=None, update_existing=False):
    print(f"Inspecting old database: {old_db_path}", flush=True)
    rows, candidates = discover_client_rows(old_db_path, table_name=table_name)
    if not candidates:
        return {"created": 0, "skipped": 0, "preview": [], "candidates": []}
    print("Candidate client tables found:", flush=True)
    for table, count in candidates:
        print(f"  - {table}: {count} rows", flush=True)
    if limit:
        rows = rows[:limit]
    print(f"Preparing {len(rows)} old CRM row(s). Loading Apex app now...", flush=True)
    from app import app
    from models import Agency, Client, db

    with app.app_context():
        agency = Agency.query.filter_by(agency_name=TARGET_AGENCY_NAME).first()
        if not agency:
            raise RuntimeError(f'Agency not found: "{TARGET_AGENCY_NAME}"')
        created = 0
        skipped = 0
        preview = []
        for index, row in enumerate(rows, start=1):
            data = row_to_client_data(row, index, a_number_column=a_number_column)
            existing = client_exists(agency.id, data, Client)
            if existing:
                if update_existing and data["a_number"] and not (existing.a_number or "").strip():
                    existing.a_number = data["a_number"]
                    db.session.add(existing)
                    created += 1
                    if len(preview) < 10:
                        preview.append(data)
                else:
                    skipped += 1
                continue
            client = Client(agency_id=agency.id, **data)
            client.username = unique_username(make_username(data["first_name"], data["last_name"], data["a_number"], index), Client)
            client.set_password(f"Imported{datetime.utcnow().strftime('%Y%m%d')}!")
            db.session.add(client)
            created += 1
            if created % 100 == 0:
                print(f"Processed {created} new client(s)...", flush=True)
            if len(preview) < 10:
                preview.append(data)
        if commit:
            db.session.commit()
        else:
            db.session.rollback()
        return {"created": created, "skipped": skipped, "preview": preview, "candidates": candidates}


def main():
    parser = argparse.ArgumentParser(description="Import basic clients from the old Odaisa CRM SQLite database into Apex CRM.")
    parser.add_argument("old_db_path", help="Path to the old odaisa_crm.db file")
    parser.add_argument("--commit", action="store_true", help="Actually save imported clients. Without this, the script previews only.")
    parser.add_argument("--table", help="Optional source table name if auto-detection picks the wrong table.")
    parser.add_argument("--limit", type=int, help="Optional maximum number of rows to process.")
    parser.add_argument("--inspect", action="store_true", help="Print old database tables, columns, and sample rows, then exit.")
    parser.add_argument("--a-number-column", help="Force the old CRM column to use as Alien/A-number.")
    parser.add_argument("--update-existing", action="store_true", help="Update already-imported clients when matched and missing an A-number.")
    args = parser.parse_args()
    if not os.path.exists(args.old_db_path):
        raise SystemExit(f"Old database file not found: {args.old_db_path}")
    if args.inspect:
        inspect_old_database(args.old_db_path, table_name=args.table)
        return
    result = import_clients(
        args.old_db_path,
        commit=args.commit,
        table_name=args.table,
        limit=args.limit,
        a_number_column=args.a_number_column,
        update_existing=args.update_existing,
    )
    print("Candidate tables:")
    for table, count in result["candidates"]:
        print(f"  - {table}: {count} rows")
    print(f"\nMode: {'COMMIT' if args.commit else 'PREVIEW ONLY'}")
    action_label = "Clients to create/update" if args.update_existing else "Clients to create"
    print(f"{action_label}: {result['created']}")
    print(f"Skipped existing clients: {result['skipped']}")
    if result["preview"]:
        print("\nPreview of first clients:")
        for client in result["preview"]:
            print(f"  - {client['first_name']} {client['last_name']} | {client['phone']} | {client['email']} | {client['a_number']}")


if __name__ == "__main__":
    main()
