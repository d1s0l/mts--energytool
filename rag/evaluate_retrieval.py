"""Transparent development-set evaluation. Not a hidden-test prediction.

Reads external demo_questions.csv and KB. Contains expectations, never answers
or routing rules used by rag_search.py. Outputs a report to stdout.
"""
import argparse
import csv
import json
import re
import statistics
from rag_search import Config, KnowledgeSearch

# At least one acceptable evidence pattern must occur in the returned text.
FACTS = {
 '1':r'запад-восток|используются оба ската',
 '2':r'вертикальн', '3':r'наращива|поэтапн|расшир',
 '4':r'шифер', '5':r'сим-картами других операторов',
 '6':r'по цехам', '7':r'сравнение.*точек|сравнение.*объект|учёт по объектам',
 '8':r'раздельно по\s+тарифам|многотарифн', '9':r'год производства',
 '10':r'профиль|пиков|дорогие часы', '11':r'сначала.*измерить',
 '12':r'аудит', '13':r'сообщим.*до старта|сообщают.*до начала|не настаив',
 '14':r'выработка не совпад|выработка совпадает|профиль потребления|накопител',
 '15':r'существующ.*прибор|что уже установлено|способ сбора|схем.*сбора',
 '16':r'не менее.*года|не менее одного года',
 '17':r'двухставоч|плата за мощность|плату за мощность',
 '18':r'количество и тип приборов|существующая схема сбора',
}

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--kb',required=True)
    p.add_argument('--questions',required=True)
    args=p.parse_args()
    r=KnowledgeSearch(args.kb,Config(mode='local'))
    rows=[]
    with open(args.questions,encoding='utf-8-sig',newline='') as f:
        for row in csv.DictReader(f):
            result=r.search(row['question'])
            hits=result['hits']
            match=[h['rank'] for h in hits if re.search(FACTS[row['id']],h['text'],re.I|re.S)]
            rows.append({'id':row['id'],'evidence_in_top6':bool(match),
                         'first_evidence_rank':min(match) if match else None,
                         'search_ms':result['search_ms'],'hits':hits})
    times=sorted(x['search_ms'] for x in rows)
    print(json.dumps({'scope':'development demo questions; regex evidence checks, not answer correctness',
       'index':r.info,'matched':sum(x['evidence_in_top6'] for x in rows),'total':len(rows),
       'mean_search_ms':statistics.mean(times),'p95_search_ms':times[max(0,__import__('math').ceil(.95*len(times))-1)],
       'rows':rows},ensure_ascii=False,indent=2))

if __name__=='__main__':
    main()
