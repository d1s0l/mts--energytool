"""Regression probes added during v2 review; development data, not hidden tests."""
import argparse
import json
import re
from rag_search import Config, KnowledgeSearch

CASES = [('У нас сеть кафе. Можно выявить необычный расход по каждой точке?', 'аномальн|сравнен'), ('Нужно ли подводить интернет для самой выработки солнечной энергии?', 'для работы станции интернет не|выработка не зависит'), ('Можно взять солнечную установку в рассрочку на несколько лет?', 'до 5 лет'), ('Что значит гарантия панелей на двадцать пять лет?', 'деградаци'), ('Нужны ли фамилии жильцов для анализа коммерческих потерь?', 'персональные данные.*не требуются|фамилии.*не запрашиваются'), ('У нас 670 кВт. Нужны ли почасовые показания?', 'почасов'), ('Можно сначала подключить только один прибор и посмотреть результат?', 'пилот|один прибор'), ('Вы можете развернуть аналитический продукт у нас на сервере?', 'не предусмотрен|не устанавливаем'), ('Какие сроки запуска типовой солнечной установки на 100 кВт?', 'около 2 месяцев'), ('Какая гарантия дается на инвертор солнечной станции?', '10.*15 лет')]

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--kb',required=True)
    args=p.parse_args()
    engine=KnowledgeSearch(args.kb,Config(mode='local'))
    rows=[]
    for question,pattern in CASES:
        matches=[h['rank'] for h in engine.search(question)['hits'] if re.search(pattern,h['text'],re.I|re.S)]
        rows.append({'question':question,'matched':bool(matches),'first_evidence_rank':min(matches) if matches else None})
    print(json.dumps({'matched':sum(r['matched'] for r in rows),'total':len(rows),'rows':rows},ensure_ascii=False,indent=2))

if __name__=='__main__':
    main()
