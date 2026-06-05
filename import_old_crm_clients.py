import argparse
import os
import re
import sqlite3
from datetime import datetime

from app import app
from models import Agency, Client, db


TARGET_AGENCY_NAME = "Odaisa Consultancy Services LLC"

FIRST_NAME_KEYS = ("first_name", "firstname", "first", "nombre")
MIDDLE_NAME_KEYS = ("middle_name", "middlename", "middle", "segundo")
LAST_NAME_KEYS = ("last_name", "lastname", "last", "apellido")
FULL_NAME_KEYS = ("full_name", "fullname", "name", "client_name", "nombre_completo")
A_NUMBER_KEYS = ("a_number", "alien_number", "anumber", "a_num", "alien", "numero_alien")
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


def unique_username(username):
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
        rows = [dict(row) for row in connection.execute(f'SELECT * FROM "{table}"')]
        return rows, [(name, count) for name, _columns, count in candidates]
    finally:
        connection.close()


def row_to_client_data(row, index):
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
        "a_number": first_present(row, A_NUMBER_KEYS),
        "phone": compact_phone(first_present(row, PHONE_KEYS)) or "000-000-0000",
        "email": first_present(row, EMAIL_KEYS) or f"imported.client.{index}@example.invalid",
        "street_address": first_present(row, STREET_KEYS) or "Address not provided",
        "apartment": first_present(row, APT_KEYS),
        "city": first_present(row, CITY_KEYS) or "Unknown",
        "state": (first_present(row, STATE_KEYS) or "NA")[:2].upper(),
        "zip_code": first_present(row, ZIP_KEYS) or "00000",
    }


def client_exists(agency_id, data):
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


def import_clients(old_db_path, commit=False, table_name=None, limit=None):
    rows, candidates = discover_client_rows(old_db_path, table_name=table_name)
    if limit:
        rows = rows[:limit]
    with app.app_context():
        agency = Agency.query.filter_by(agency_name=TARGET_AGENCY_NAME).first()
        if not agency:
            raise RuntimeError(f'Agency not found: "{TARGET_AGENCY_NAME}"')
        created = 0
        skipped = 0
        preview = []
        for index, row in enumerate(rows, start=1):
            data = row_to_client_data(row, index)
            existing = client_exists(agency.id, data)
            if existing:
                skipped += 1
                continue
            client = Client(agency_id=agency.id, **data)
            client.username = unique_username(make_username(data["first_name"], data["last_name"], data["a_number"], index))
            client.set_password(f"Imported{datetime.utcnow().strftime('%Y%m%d')}!")
            db.session.add(client)
            created += 1
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
    args = parser.parse_args()
    if not os.path.exists(args.old_db_path):
        raise SystemExit(f"Old database file not found: {args.old_db_path}")
    result = import_clients(args.old_db_path, commit=args.commit, table_name=args.table, limit=args.limit)
    print("Candidate tables:")
    for table, count in result["candidates"]:
        print(f"  - {table}: {count} rows")
    print(f"\nMode: {'COMMIT' if args.commit else 'PREVIEW ONLY'}")
    print(f"Clients to create: {result['created']}")
    print(f"Skipped existing clients: {result['skipped']}")
    if result["preview"]:
        print("\nPreview of first clients:")
        for client in result["preview"]:
            print(f"  - {client['first_name']} {client['last_name']} | {client['phone']} | {client['email']} | {client['a_number']}")


if __name__ == "__main__":
    main()
