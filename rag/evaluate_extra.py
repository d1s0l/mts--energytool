"""Additional development probes; expectations are never imported by retrieval."""
import argparse
import json
import re
from rag_search import Config, KnowledgeSearch

CASES = [
 ('Сможем ли мы получать энергию от сетевой солнечной станции при аварии в электросети?',r'отключается|не работает'),
 ('Нужен доступ к показаниям сорока арендаторов бизнес-центра.',r'арендатор'),
 ('На предприятии 500 кВт. Доступен ли выход на оптовый рынок?',r'750'),
 ('Мощность 25 МВт: можно участвовать в оптовом рынке напрямую?',r'20 МВт'),
 ('Будет ли платформа сама выписывать квитанции в рублях?',r'не выставля|не занимается'),
 ('Нужно ли покупать свой сервер для облачного учёта?',r'не требуется|не требуются'),
 ('Сколько лет хранится архив фотографий приборов?',r'срок хранения|столько, сколько'),
 ('Два завода не соединены проводами. Можно сложить их мощности?',r'связност'),
 ('Можно ли распознавать газовый счётчик по фотографии?',r'доработк'),
 ('Можете назвать производителя панелей?',r'не.*[Нн]азывать производителей|[Нн]азывать производителей'),
 ('Гарантируете ли вы экономию ровно 15 процентов?',r'не гаранти|не обещан|не гарантия'),
 ('Что потребуется для оценки учёта на складе?',r'количество и тип приборов|существующая схема сбора'),
]

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--kb',required=True)
    args=p.parse_args()
    r=KnowledgeSearch(args.kb,Config(mode='local'))
    rows=[]
    for question,pattern in CASES:
        result=r.search(question)
        matches=[h['rank'] for h in result['hits'] if re.search(pattern,h['text'],re.I|re.S)]
        rows.append({'question':question,'matched':bool(matches),'first_evidence_rank':min(matches) if matches else None})
    print(json.dumps({'scope':'additional development probes, not independent held-out data',
                     'matched':sum(x['matched'] for x in rows),'total':len(rows),'rows':rows},ensure_ascii=False,indent=2))

if __name__=='__main__':
    main()
