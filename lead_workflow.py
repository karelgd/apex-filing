from datetime import timedelta

from sqlalchemy import or_

from models import CrmLead, CrmLeadTask, Notification, db


LEAD_NOTE_TYPES = {
    "call": "Call",
    "text": "Text message",
    "whatsapp": "WhatsApp",
    "email": "Email",
}
LEAD_TAGS = ("New", "Assigned", "Not interested")
FOLLOW_UP_TITLE = "Follow-up"
FOLLOW_UP_DESCRIPTION = (
    "Follow up with this lead today. Start by calling, leave a voicemail, and then send a text message."
)
FOLLOW_UP_INTERVAL = timedelta(days=2)
FOLLOW_UP_ATTEMPTS = 3


def active_leads(agency_id):
    return CrmLead.query.filter_by(agency_id=agency_id, converted_at=None)


def lock_active_lead(agency_id, lead_id):
    # Take a write lock before reading so SQLite also serializes claims and conversions.
    query = active_leads(agency_id).filter_by(id=lead_id)
    if query.update({CrmLead.tag: CrmLead.tag}, synchronize_session=False) != 1:
        return None
    return query.populate_existing().one()


def open_lead_tasks(lead_id):
    return CrmLeadTask.query.filter_by(lead_id=lead_id, completed_at=None, cancelled_at=None)


def cancel_lead_tasks(lead_id, now, automatic_only=False):
    query = open_lead_tasks(lead_id)
    if automatic_only:
        query = query.filter_by(is_automatic=True)
    query.update({CrmLeadTask.cancelled_at: now}, synchronize_session=False)


def reset_follow_up(lead, today, now):
    cancel_lead_tasks(lead.id, now, automatic_only=True)
    lead.follow_up_from = today
    lead.follow_up_count = 0


def alert_due_lead_tasks(lead, today, now):
    tasks = open_lead_tasks(lead.id).filter(
        CrmLeadTask.due_on <= today, CrmLeadTask.alerted_at.is_(None),
    ).all()
    for task in tasks:
        db.session.add(Notification(
            agency_id=lead.agency_id, recipient_role="agency_case_manager",
            recipient_id=lead.assigned_case_manager_id,
            notification_type="lead_task_due", lead_id=lead.id, created_at=now,
            message=f"Task due for {lead.name}: {task.title}",
        ))
        task.alerted_at = now
    return len(tasks)


def process_lead_reminders(agency_id, today, now):
    due_task = db.session.query(CrmLeadTask.id).filter(
        CrmLeadTask.lead_id == CrmLead.id,
        CrmLeadTask.completed_at.is_(None),
        CrmLeadTask.cancelled_at.is_(None),
        CrmLeadTask.alerted_at.is_(None),
        CrmLeadTask.due_on <= today,
    ).exists()
    lead_ids = [lead_id for lead_id, in active_leads(agency_id).with_entities(CrmLead.id).filter(
        CrmLead.assigned_case_manager_id.is_not(None),
        or_(CrmLead.tag == "Assigned", due_task),
    )]
    db.session.rollback()
    generated = alerted = closed = 0
    for lead_id in lead_ids:
        lead = lock_active_lead(agency_id, lead_id)
        manager = lead.assigned_case_manager if lead else None
        if not manager or not manager.is_active or manager.agency_id != agency_id:
            db.session.rollback()
            continue
        if lead.tag == "Assigned" and lead.follow_up_from:
            expires_on = lead.follow_up_from + FOLLOW_UP_INTERVAL * (FOLLOW_UP_ATTEMPTS + 1)
            if today >= expires_on:
                cancel_lead_tasks(lead.id, now, automatic_only=True)
                lead.tag = "Not interested"
                closed += 1
            else:
                while lead.follow_up_count < FOLLOW_UP_ATTEMPTS:
                    due_on = lead.follow_up_from + FOLLOW_UP_INTERVAL * (lead.follow_up_count + 1)
                    if due_on > today:
                        break
                    db.session.add(CrmLeadTask(
                        lead_id=lead.id, title=FOLLOW_UP_TITLE, description=FOLLOW_UP_DESCRIPTION,
                        due_on=due_on, is_automatic=True, created_at=now,
                    ))
                    lead.follow_up_count += 1
                    generated += 1
        alerted += alert_due_lead_tasks(lead, today, now)
        db.session.commit()
    return generated, alerted, closed
