"""Repair visual review placements for the legacy G-1450 questionnaire.

Older Form Filler questionnaires can have PDF fields linked to questions without
having the newer PdfQuestionPlacement rows used by the agency review screen.
This script copies the linked PDF field rectangles into visual placements for
G-1450 only. It is safe to run more than once.
"""

import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app import app, pdf_field_visual_mapping
from models import CaseQuestion, FormTemplate, PdfField, PdfQuestionPlacement, db


TARGET_CODE = "G1450"


def normalized_code(value):
    return "".join(character for character in (value or "").upper() if character.isalnum())


def placement_key(placement):
    return (
        int(placement.page_number or 0),
        round(float(placement.x or 0), 2),
        round(float(placement.y or 0), 2),
        round(float(placement.width or 0), 2),
        round(float(placement.height or 0), 2),
    )


def mapping_key(mapping):
    return (
        int(mapping["page"] or 0),
        round(float(mapping["x"] or 0), 2),
        round(float(mapping["y"] or 0), 2),
        round(float(mapping["width"] or 0), 2),
        round(float(mapping["height"] or 0), 2),
    )


def main():
    with app.app_context():
        templates = [
            template
            for template in FormTemplate.query.order_by(FormTemplate.id).all()
            if normalized_code(template.code) == TARGET_CODE
        ]
        if not templates:
            print("No G-1450 template found.")
            return

        total_added = 0
        total_skipped = 0
        for template in templates:
            fields = (
                PdfField.query.filter(
                    PdfField.template_id == template.id,
                    PdfField.mapped_question_id.isnot(None),
                )
                .order_by(PdfField.page_number, PdfField.id)
                .all()
            )
            added = 0
            skipped = 0
            for field in fields:
                question = db.session.get(CaseQuestion, field.mapped_question_id)
                mapping = pdf_field_visual_mapping(field)
                if not question or not mapping:
                    skipped += 1
                    continue
                existing_keys = {placement_key(placement) for placement in question.placements}
                key = mapping_key(mapping)
                if key in existing_keys:
                    skipped += 1
                    continue
                db.session.add(
                    PdfQuestionPlacement(
                        question_id=question.id,
                        page_number=mapping["page"],
                        x=mapping["x"],
                        y=mapping["y"],
                        width=mapping["width"],
                        height=mapping["height"],
                    )
                )
                added += 1
            db.session.commit()
            total_added += added
            total_skipped += skipped
            print(f"{template.code}: added {added} visual placement(s), skipped {skipped}.")
        print(f"Done. Total added: {total_added}. Total skipped: {total_skipped}.")


if __name__ == "__main__":
    main()
