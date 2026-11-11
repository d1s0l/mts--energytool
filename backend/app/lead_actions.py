"""Closed server-side action. Caller holds the session lock and transaction."""
from app.schemas import LeadIn


async def create_lead(conn, *, session_id, contacts, summary):
    contacts = LeadIn.model_validate(contacts)
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 20000 or '\x00' in summary:
        raise ValueError('Invalid lead summary')
    cursor = await conn.execute(
        'SELECT id FROM leads WHERE session_id = %s ORDER BY created_at DESC LIMIT 1', (session_id,))
    existing = await cursor.fetchone()
    values = (contacts.full_name, contacts.phone, contacts.company, summary.strip())
    if existing:
        await conn.execute('UPDATE leads SET full_name = %s, phone = %s, company = %s, summary = %s '
                           'WHERE id = %s', (*values, existing['id']))
        return existing['id']
    cursor = await conn.execute(
        'INSERT INTO leads(session_id, full_name, phone, company, summary) '
        'VALUES (%s, %s, %s, %s, %s) RETURNING id', (session_id, *values))
    return (await cursor.fetchone())['id']


async def record_contact_memory(conn, *, session_id, contacts):
    """A form submission is a user event, so contact corrections have durable provenance."""
    from dialog_memory import merge_memory
    from psycopg.types.json import Jsonb
    contacts = LeadIn.model_validate(contacts)
    content = f'ФИО: {contacts.full_name}\nТелефон: {contacts.phone}\nКомпания: {contacts.company}'
    cursor = await conn.execute(
        "INSERT INTO messages(session_id, role, text) VALUES (%s, 'user', %s) RETURNING id",
        (session_id, content))
    source = str((await cursor.fetchone())['id'])
    cursor = await conn.execute('SELECT dialog_memory FROM sessions WHERE id = %s', (session_id,))
    previous = (await cursor.fetchone())['dialog_memory']
    cursor = await conn.execute(
        'SELECT id::text AS message_id, role, text AS content FROM messages WHERE session_id = %s ORDER BY position',
        (session_id,))
    messages = await cursor.fetchall()
    updates = [dict(field=key, scope='client', status='provided', value=value,
                    source_message_id=source, quote=value) for key,value in contacts.model_dump().items()]
    memory = merge_memory(previous, updates, messages)
    cursor = await conn.execute('SELECT id FROM leads WHERE session_id = %s ORDER BY created_at DESC LIMIT 1',
                                (session_id,))
    lead = await cursor.fetchone()
    if lead:
        memory['lead'] = {'id': str(lead['id']), 'status': 'saved'}
    await conn.execute('UPDATE sessions SET dialog_memory = %s, dialog_revision = dialog_revision + 1 '
                       'WHERE id = %s', (Jsonb(memory), session_id))
