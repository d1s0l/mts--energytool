"""Session-local facts with user-message provenance. No product knowledge is stored here."""
import copy
import hashlib
import re

FIELDS = {
    'full_name', 'phone', 'company', 'goal', 'organization_kind', 'region',
    'roof_age', 'device_models', 'existing_workshop_meters', 'daytime_consumption',
    'day_night_split', 'meter_count', 'collection_method', 'connected_power',
    'monthly_consumption', 'price_category', 'archive_depth', 'resource',
    'contact_permission', 'active_object',
}
CLIENT_FIELDS = {'full_name', 'phone', 'company', 'contact_permission', 'active_object'}
STATUSES = {'provided', 'tentative', 'unknown', 'declined'}


def indexed_messages(history, text, message_id=None):
    messages = []
    for n, message in enumerate(history):
        messages.append({**message, 'message_id': str(message.get('message_id') or f'history:{n}')})
    messages.append({'role': 'user', 'content': text,
                     'message_id': str(message_id or f'history:{len(history)}')})
    return messages


def empty_memory():
    return {'schema_version': 1, 'revision': 0, 'facts': [], 'asked': []}


def object_reference(text, scopes):
    """Resolve a unique existing object name, allowing conservative Russian inflections."""
    def stem(word):
        word = word.casefold().replace('ё', 'е')
        shortened = re.sub(r'(?:ому|ему|ого|его|ыми|ими|ый|ий|ой|ая|яя|ую|юю|ое|ее|ым|им|ом|ем|ам|ям|ах|ях|а|у|е|ы|и)$', '', word)
        return shortened if len(shortened) >= 3 else word
    words = list(re.finditer(r'[\w]+', text, re.UNICODE))
    normalized = [stem(w.group()) for w in words]
    matches = []
    for scope in scopes:
        tokens = [stem(w) for w in re.findall(r'[\w]+', scope, re.UNICODE)]
        if not tokens:
            continue
        for n in range(len(words)-len(tokens)+1):
            literal = text[words[n].start():words[n+len(tokens)-1].end()]
            # A single word must match literally; no fuzzy organization/name guesses.
            if normalized[n:n+len(tokens)] == tokens and (len(tokens) > 1 or literal.casefold() == scope.casefold()):
                matches.append((scope, literal))
                break
    return matches[0] if len(matches) == 1 else None


def select_active_object(memory, messages):
    """A unique explicit reference selects an existing object before generation."""
    scopes = {f['scope'] for f in memory['facts']} - {'client', 'default'}
    current = messages[-1]
    declaration=re.match(r'\s*((?:первый|второй|третий|четв[её]ртый|пятый|шестой|\d+[- ]?й)\s+'
                         r'(?:склад|объект|цех|магазин|завод))\b[^:\n]{0,80}:',current['content'],re.I)
    if declaration:
        label=declaration.group(1)
        reference=object_reference(label,scopes) or (label,label)
    else:
        reference = object_reference(current['content'], scopes)
    if not reference or reference[0] == active_scope(memory):
        return memory
    scope, quote = reference
    return merge_memory(memory, [dict(field='active_object', scope='client', status='provided',
        value=scope, quote=quote, source_message_id=current['message_id'])], messages)


def contact_permission(text):
    """Recognize explicit contact consent/refusal; other topics give no decision."""
    refused = re.search(
        r'не (?:хочу|готов|буду|дам|согласен|разрешаю|оформляйте|оставляйте).{0,50}(?:телефон|контакт|заявк|звон)|'
        r'(?:телефон|контакт|заявк).{0,35}не (?:хочу|готов|буду|дам|оформляйте|оставляйте)|не звоните|без звонк', text, re.I)
    if refused:
        return 'declined', refused.group()
    allowed = re.search(
        r'(?:хочу|готов|давайте|оформите|оставлю|согласен|передайте).{0,40}(?:заявк|контакт|телефон|звон)|свяжитесь|позвоните|\bзвоните\b|перезвоните|можете позвонить', text, re.I)
    return ('provided', allowed.group()) if allowed else None


def refresh_contact_permission(memory, messages):
    current = messages[-1]
    decision = contact_permission(current['content'])
    if not decision:
        return memory
    status, quote = decision
    return merge_memory(memory, [dict(field='contact_permission', scope='client', status=status,
        value=quote if status == 'provided' else None, quote=quote,
        source_message_id=current['message_id'])], messages)


def refresh_contact_phone(memory, messages):
    """An unambiguous explicit phone correction cannot resurrect an older number."""
    current = messages[-1]
    decision = contact_permission(current['content'])
    if decision and decision[0] == 'declined':
        return memory
    correcting = re.search(r'исправ|замен|обнов|ошибочн', current['content'], re.I)
    known = (any(f['field'] == 'phone' for f in memory['facts']) or memory.get('lead')
             or re.search(r'(?:телефон|номер).{0,25}(?:заявк|связ)|в заявке', current['content'], re.I))
    if not ((decision and decision[0] == 'provided') or (correcting and known)):
        return memory
    phones = re.findall(r'\+?\d[\d ()-]{8,30}\d', current['content'])
    valid = [(raw, re.sub(r'[ ()-]', '', raw)) for raw in phones]
    valid = [(raw, value) for raw, value in valid if re.fullmatch(r'\+?\d{10,15}', value)]
    if len({value for _, value in valid}) != 1:
        return memory
    quote, value = valid[0]
    return merge_memory(memory, [dict(field='phone', scope='client', status='provided', value=value,
        quote=quote, source_message_id=current['message_id'])], messages)


def remember_lead_contacts(memory, contacts, messages):
    """Durably store already validated contacts even if optional model deltas omitted them."""
    known={f['field'] for f in memory['facts'] if f['scope']=='client'}
    updates=[]
    for field,value in contacts.items():
        if field in known:continue
        for message in reversed(messages):
            if message['role']!='user':continue
            if field=='phone':
                quote=next((p for p in re.findall(r'\+?\d[\d ()-]{8,30}\d',message['content'])
                            if re.sub(r'[ ()-]','',p)==value),None)
            else:
                match=re.search(re.escape(value),message['content'],re.I)
                quote=match.group() if match else None
            if quote:
                updates.append(dict(field=field,scope='client',status='provided',value=value,
                    quote=quote,source_message_id=message['message_id']))
                break
    return merge_memory(memory,updates,messages)


def merge_memory(previous, updates, messages):
    """Only quoted user statements can change facts; older turns cannot undo corrections."""
    memory = copy.deepcopy(previous or empty_memory())
    if memory.get('schema_version') != 1 or not isinstance(memory.get('facts'), list):
        raise ValueError('Unsupported dialog memory schema')
    if not isinstance(updates, list) or len(updates) > 24:
        raise ValueError('memory_updates must be a bounded list')
    users = {m['message_id']: (n, m['content']) for n, m in enumerate(messages) if m['role'] == 'user'}
    facts = {(f['scope'], f['field']): f for f in memory['facts']}
    for proposal in updates:
        if not isinstance(proposal, dict):
            raise ValueError('Invalid memory update')
        field, status = proposal.get('field'), proposal.get('status')
        value, quote = proposal.get('value'), proposal.get('quote')
        source = proposal.get('source_message_id')
        scope = proposal.get('scope', 'client' if field in CLIENT_FIELDS else 'default')
        if field not in FIELDS or status not in STATUSES or source not in users:
            raise ValueError('Memory needs an allowed field, status and a user source ID')
        if (not isinstance(quote, str) or not quote or len(quote) > 1500
                or quote not in users[source][1]):
            raise ValueError('Memory quote must occur verbatim in its user source')
        if not isinstance(scope, str) or not scope or len(scope) > 160:
            raise ValueError('Invalid object scope')
        if field in CLIENT_FIELDS and scope != 'client':
            raise ValueError('Contact and active-object fields belong to client scope')
        if field not in CLIENT_FIELDS and scope == 'client':
            raise ValueError('Object facts need an object scope')
        if scope not in {'client', 'default'} and not any(scope.casefold() in text.casefold() for _, text in users.values()):
            raise ValueError('Object scope must be named by the user')
        if status == 'provided' and re.search(r'кажется|вроде|не уверен', quote, re.I):
            raise ValueError('Uncertain user statement must remain tentative')
        if status in {'provided', 'tentative'}:
            if not isinstance(value, str) or not value.strip() or len(value) > 500 or '\x00' in value:
                raise ValueError('Invalid memory value')
            if field == 'phone':
                normalized = lambda s: re.sub(r'[ ()-]', '', s)
                if not re.fullmatch(r'\+?\d{10,15}', normalized(value)) or normalized(value) not in normalized(quote):
                    raise ValueError('Memory phone must match user input')
                value = normalized(value)
            elif value.casefold() not in quote.casefold():
                scopes = {f['scope'] for f in memory['facts']} - {'client', 'default'}
                reference = object_reference(quote, scopes) if field == 'active_object' else None
                if not reference or reference[0] != value:
                    raise ValueError('Memory value must be a literal part of its quote')
        elif status == 'unknown' and not re.search(r'не знаю|не помню|неизвест|нет данных|не могу сказать|не уверен', quote, re.I):
            raise ValueError('Unknown status requires a statement of uncertainty')
        elif status == 'declined' and not re.search(r'не (?:хочу|буду|готов|дам|сообщ|оформляйте|оставляйте)|не звоните|отказ|без звонк', quote, re.I):
            raise ValueError('Declined status requires a refusal')
        if status in {'unknown', 'declined'} and value is not None:
            raise ValueError('Unknown/declined values must be null')
        if field == 'contact_permission':
            decision = contact_permission(quote)
            if not decision or decision[0] != status:
                raise ValueError('Contact permission requires explicit contact consent or refusal')
        if field == 'active_object' and value not in {'default', None}:
            if not any(value.casefold() in text.casefold() for _, text in users.values()):
                raise ValueError('Active object must be named by the user')
        current = facts.get((scope, field))
        if current:
            old_order = users.get(current['source_message_id'], (-1, ''))[0]
            if users[source][0] < old_order:
                continue
            if source == current['source_message_id'] and (value, status) != (current['value'], current['status']):
                raise ValueError('Conflicting facts from the same user message')
        facts[(scope, field)] = dict(field=field, scope=scope, status=status, value=value,
                                    quote=quote, source_message_id=source)
    memory['facts'] = list(facts.values())
    if updates:
        memory['revision'] += 1
    return memory


def active_scope(memory):
    for fact in memory.get('facts', []):
        if fact['field'] == 'active_object' and fact['status'] == 'provided':
            return fact['value']
    return 'default'


def known_fields(memory):
    scope = active_scope(memory)
    # "Don't know" and refusals also stop repetitive interrogation; tentative values do not.
    return {f['field'] for f in memory.get('facts', [])
            if f['scope'] in {'client', scope} and f['status'] in {'provided', 'unknown', 'declined'}}


def check_lead_against_memory(lead, memory):
    for fact in memory.get('facts', []):
        if fact['scope'] == 'client' and fact['field'] in {'full_name', 'phone', 'company'}:
            if fact['status'] != 'provided' or lead[fact['field']].casefold() != fact['value'].casefold():
                raise ValueError('Lead conflicts with current contacts or a contact refusal')
        if fact['field'] == 'contact_permission' and fact['status'] == 'declined':
            raise ValueError('Contact permission declined')


def remember_question(memory, field, message_id):
    if not field:
        return memory
    memory = copy.deepcopy(memory)
    item = {'field': field, 'scope': 'client' if field in CLIENT_FIELDS else active_scope(memory), 'after_user_message_id': message_id}
    if item not in memory['asked']:
        memory['asked'].append(item)
    memory['asked'] = memory['asked'][-50:]
    return memory


def question_field(question):
    q = (question or '').lower().replace('ё', 'е')
    patterns = {
        'phone': r'телефон|номер.*связ',
        'full_name': r'как вас зовут|фио|ваш[аи] фамил',
        'company': r'(?:назван|называется).*компани|компани.*называется',
        'device_models': r'модел.*(?:прибор|счетчик)|(?:прибор|счетчик).*модел',
        'meter_count': r'сколько.*(?:прибор|счетчик)|количеств.*(?:прибор|счетчик)',
        'roof_age': r'сколько лет.*(?:кровл|крыш)|возраст.*(?:кровл|крыш)',
        'connected_power': r'присоединенн.*мощност',
        'region': r'каком регионе|какой регион',
        'day_night_split': r'разбивк.*(?:ноч|днев|день)',
        'collection_method': r'как.*собира.*показан|(?:способ|схем).*сбор.*(?:данн|показан)',
        'price_category': r'какая.*ценов.*категори|какой.*ценов.*категори',
        'resource': r'какие ресурсы|какой ресурс|электроэнерги.*(?:или|и) вод',
        'existing_workshop_meters': r'(?:есть|стоят|установлен).*счетчик.*цех|счетчик.*цех.*(?:есть|стоят|установлен)',
    }
    return next((field for field, pattern in patterns.items() if re.search(pattern, q)), None)


def question_slot(question):
    """Track every pending question, even when its wording has no predefined field."""
    if not isinstance(question,str) or not question.strip():return None
    question=re.split(r'(?<=[.!?])\s+',question.strip())[-1]
    field=question_field(question)
    if field:return field
    normalized=' '.join(question.casefold().replace('ё','е').split())
    return 'question:'+hashlib.sha256(normalized.encode()).hexdigest()[:16]


def manager_summary(memory, fallback):
    labels = {'goal':'Задача', 'organization_kind':'Тип организации', 'region':'Регион',
              'roof_age':'Возраст кровли', 'device_models':'Модели приборов',
              'existing_workshop_meters':'Учёт по цехам', 'daytime_consumption':'Дневная нагрузка',
              'day_night_split':'Разбивка день/ночь', 'meter_count':'Количество приборов',
              'collection_method':'Сбор показаний', 'connected_power':'Присоединённая мощность',
              'monthly_consumption':'Месячное потребление', 'price_category':'Ценовая категория',
              'archive_depth':'Глубина архива', 'resource':'Ресурс'}
    lines=[]
    for fact in memory['facts']:
        if fact['scope'] != active_scope(memory) or fact['field'] not in labels:
            continue
        value = fact['value']
        if fact['status'] == 'tentative':
            value = f'{value} (клиент не уверен)'
        elif fact['status'] == 'unknown':
            value = 'клиент не знает'
        elif fact['status'] == 'declined':
            value = 'клиент отказался сообщать'
        lines.append(f"{labels[fact['field']]}: {value}")
    # Current validated values take precedence over older quotes in a draft summary.
    return ('Актуальные данные со слов клиента:\n' + '\n'.join(lines)) if lines else fallback


def verified_memory_updates(previous, updates, messages):
    """Accept independently verified facts; optional extraction cannot abort the answer."""
    state = merge_memory(previous, [], messages)
    warnings = []
    if not isinstance(updates, list):
        return state, ['memory_updates_not_a_list']
    ordered = sorted(enumerate(updates[:24]), key=lambda pair:
                     0 if isinstance(pair[1], dict) and pair[1].get('field') == 'active_object' else 1)
    for index, raw in ordered:
        try:
            if not isinstance(raw, dict):
                raise ValueError('invalid_item')
            item = dict(raw)
            field = item.get('field')
            if field in CLIENT_FIELDS:
                item['scope'] = 'client'
            elif item.get('scope') in {None, 'client', 'default'}:
                item['scope'] = active_scope(state)
            if field == 'active_object' and isinstance(item.get('quote'), str):
                scopes = {f['scope'] for f in state['facts']} - {'client', 'default'}
                reference = object_reference(item['quote'], scopes)
                if reference and item.get('status') == 'provided':
                    item['value'] = reference[0]
            # Preserve the actual user wording rather than the model's paraphrase.
            if (field not in {'phone', 'full_name', 'company', 'active_object'}
                    and item.get('status') in {'provided', 'tentative'}
                    and isinstance(item.get('quote'), str)
                    and (not isinstance(item.get('value'), str)
                         or item['value'].casefold() not in item['quote'].casefold())):
                item['value'] = item['quote']
            quote = item.get('quote', '')
            if field == 'day_night_split' and item.get('status') in {'provided', 'tentative'} and not (
                    re.search(r'день|дневн', quote, re.I) and re.search(r'ноч', quote, re.I)):
                raise ValueError('day_night_split_requires_both_zones')
            if field == 'daytime_consumption' and item.get('status') in {'provided', 'tentative'} and not re.search(r'днем|днём|дневн', quote, re.I):
                raise ValueError('daytime_load_requires_daytime_statement')
            state = merge_memory(state, [item], messages)
        except (ValueError, TypeError, KeyError) as exc:
            # Static validation messages, never the rejected value or a model exception body.
            reason = str(exc) if type(exc) is ValueError else type(exc).__name__
            warnings.append(f'item_{index}: {reason}')
    if len(updates) > 24:
        warnings.append('memory_updates_truncated_to_24')
    return state, warnings
