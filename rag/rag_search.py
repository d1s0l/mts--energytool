"""EnergyTool retrieval only. Python 3.10+, standard library, no generation.

Create one KnowledgeSearch at application startup; share it between requests.
The supplied knowledge base stays outside this module and outside the code ZIP.
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
import logging
import math
import os
import re
import threading
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath


LOGGER = logging.getLogger("rag_search")
LOGGER.addHandler(logging.NullHandler())


def _env(name, default=""):
    return os.getenv(name, "").strip() or default


@dataclass(frozen=True)
class Config:
    mode: str = "local"  # local, hybrid, auto
    top_k: int = 6
    chunk_chars: int = 1800
    overlap_chars: int = 240
    candidates: int = 30
    base_url: str = ""
    api_key: str = field(default="", repr=False)
    embedding_model: str = "BAAI/bge-m3"
    reranker_model: str = ""
    reranker_top_n: int = 30
    timeout: float = 15.0
    retries: int = 1
    batch_size: int = 32
    max_input_bytes: int = 32_000_000

    def __post_init__(self):
        if self.mode not in {"local", "hybrid", "auto"}:
            raise ValueError("mode must be local, hybrid or auto")
        for key in ("top_k", "candidates", "batch_size", "max_input_bytes", "reranker_top_n"):
            if getattr(self, key) < 1:
                raise ValueError(f"{key} must be positive")
        if self.chunk_chars < 300 or not 0 <= self.overlap_chars < self.chunk_chars:
            raise ValueError("invalid chunk/overlap sizes")
        if self.timeout <= 0 or not 0 <= self.retries <= 2:
            raise ValueError("invalid timeout or retries")

    @classmethod
    def from_env(cls):
        return cls(
            mode=_env("RAG_MODE", "auto"), top_k=int(_env("TOP_K", "6")),
            chunk_chars=int(_env("RAG_CHUNK_CHARS", "1800")),
            overlap_chars=int(_env("RAG_OVERLAP_CHARS", "240")),
            candidates=int(_env("RAG_CANDIDATES", "30")),
            base_url=_env("LLM_BASE_URL") or _env("OPENAI_BASE_URL"),
            api_key=_env("LLM_API_KEY") or _env("OPENAI_API_KEY"),
            embedding_model=_env("EMB_MODEL", "BAAI/bge-m3"),
            reranker_model=_env("RERANKER_MODEL"),
            reranker_top_n=int(_env("RERANKER_TOP_N", "30")),
            timeout=float(_env("RAG_API_TIMEOUT", "15")),
            retries=int(_env("RAG_API_RETRIES", "1")),
        )


STOP = set("и в во на с со по для у мы вы я а но или как что это ли нам нас вас ваш наш бы к из от до за при он она они его ее все уже можно нужно есть будет чтобы чем если то не да какой какая какие сколько так же еще только об о их мне тебе свой после без над под через сначала вообще стоит хотим хочу понять кажется примерно три сорок больше всего готовы готов можете сможете".split())
SUFFIX = re.compile(r"(иями|ями|ами|ого|ему|ому|ыми|ими|иях|ах|ях|ией|ий|ый|ой|ая|яя|ое|ее|ые|ие|ов|ев|ам|ям|ом|ем|ую|юю|ы|и|а|я|у|ю|е|о)$")


def _terms(text):
    words = re.findall(r"[a-zа-я0-9]+", text.lower().replace("ё", "е"))
    return [SUFFIX.sub("", w) if len(w) > 4 else w for w in words if w not in STOP]


def _grams(text):
    result = collections.Counter()
    for word in _terms(text):
        word = " " + word + " "
        result.update(word[i:i+4] for i in range(max(0, len(word)-3)))
    return result


# General domain vocabulary; no question-ID routing or embedded answers.
GROUPS = [
    "солнечный панели солнце фотоэлектрический", "крыша кровля скат",
    "электричество электроэнергия свет", "счетчик прибор учет",
    "сим симка sim симкарта", "расход потребление", "кабинет интерфейс платформа",
    "расширить расширение наращивать поэтапный", "вечер вечерний ночь ночной",
    "реактивный компенсация компенсирующий", "хищение потери воровство",
    "телефон контакты заявка", "выпуск производство", "проверка проверите проверить аудит обследование",
    "двухтарифный многотарифный раздельно тарифы", "фото фотография фотографии распознавание",
    "оценка расчет", "понадобится понадобиться потребуется требоваться сведения данные",
    "газ газовый", "тепло тепловой", "счетчик электросчетчик",
    "назвать называть раскрывать",
    "фасад вертикальный поверхность", "магазин ретейл объект точка",
]
SYNONYMS = {}
for _group in GROUPS:
    _ts = set(_terms(_group))
    for _t in _ts:
        SYNONYMS.setdefault(_t, set()).update(_ts - {_t})
for _t in SYNONYMS:
    _connected = {_t} | SYNONYMS[_t]
    while True:
        _expanded = _connected | set().union(*(SYNONYMS.get(t, set()) for t in _connected))
        if _expanded == _connected:
            break
        _connected = _expanded
    SYNONYMS[_t] = _connected - {_t}

# Query expansions describe intent, never supply answer facts.
PHRASES = {
    "что понадобится": "сведения данные количество тип приборов схема сбора",
    "для оценки": "сведения данные объекта конфигурация",
    "с чего начать": "существующие приборы схема сбора данные объекта",
    "хватит чтобы начать": "минимальные требования глубина архива",
    "проверить в счете": "объем цена плата за мощность",
    "другого оператора": "сим карты связь",
    "фамилии": "персональные данные",
}

# Soft product priors. Match document titles rather than filenames, so renamed
# files still work. Other sources always remain eligible for retrieval.
TOPICS = [
    ("солнеч", ("панел", "солнеч", "кровл", "крыша", "фасад", "инвертор", "накопител")),
    ("платформа учет", ("платформ", "кабинет", "цех", "арендатор", "магазин", "сим")),
    ("распознаван показан", ("фото", "распозна", "штрих", "год выпуск")),
    ("коммерческ потер", ("потер", "хищен", "электросет", "выезд")),
    ("оптов", ("оптов", "орэм", "750", "20 мвт")),
    ("ценов категор", ("ценов", "категор", "четверт", "пят", "шест")),
    ("расчет объект", ("что понадоб", "для оценки", "с чего начать", "какие сведения")),
]


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    source: str
    title: str
    section: str
    text: str
    line_start: int  # one-based inclusive
    line_end: int


def _documents(path, limit):
    path = Path(path)
    if not path.exists():
        LOGGER.error("rag_kb_load_failed reason=knowledge_base_not_found path=%s", path)
        raise FileNotFoundError(f"knowledge base not found: {path}")
    total = 0
    docs = []
    if path.is_dir():
        root = path.resolve()
        for p in sorted(path.rglob("*")):
            if p.is_symlink() or not p.is_file() or p.suffix.lower() not in {".md", ".txt"}:
                continue
            if not p.resolve().is_relative_to(root):
                continue
            name = p.relative_to(path).as_posix()
            try:
                size = p.stat().st_size
                if total + size > limit:
                    LOGGER.error(
                        "rag_kb_load_failed reason=byte_limit_exceeded source=%s "
                        "limit_bytes=%d",
                        name, limit,
                    )
                    raise ValueError("knowledge base exceeds byte limit")
                raw = p.read_bytes()
                if total + len(raw) > limit:
                    LOGGER.error(
                        "rag_kb_load_failed reason=byte_limit_exceeded source=%s "
                        "limit_bytes=%d",
                        name, limit,
                    )
                    raise ValueError("knowledge base exceeds byte limit")
                text = raw.decode("utf-8-sig")
            except UnicodeError as exc:
                LOGGER.error(
                    "rag_document_read_failed source=%s reason=invalid_utf8 error_type=%s",
                    name, type(exc).__name__,
                )
                raise ValueError(f"cannot decode knowledge document '{name}' as UTF-8") from exc
            except OSError as exc:
                LOGGER.error(
                    "rag_document_read_failed source=%s reason=read_error error_type=%s",
                    name, type(exc).__name__,
                )
                raise OSError(f"cannot read knowledge document '{name}'") from exc
            total += len(raw)
            docs.append((name, text))
    elif path.is_file():
        try:
            is_zip = zipfile.is_zipfile(path)
        except OSError as exc:
            LOGGER.error(
                "rag_kb_load_failed reason=archive_read_error path=%s error_type=%s",
                path, type(exc).__name__,
            )
            raise OSError(f"cannot read knowledge-base archive: {path}") from exc
        if not is_zip:
            LOGGER.error("rag_kb_load_failed reason=invalid_zip path=%s", path)
            raise ValueError(f"knowledge-base file is not a readable ZIP archive: {path}")
        try:
            with zipfile.ZipFile(path) as z:
                seen = set()
                for info in sorted(z.infolist(), key=lambda i: i.filename):
                    name = info.filename
                    p = PurePosixPath(name)
                    if info.is_dir() or p.suffix.lower() not in {".md", ".txt"} or "__MACOSX" in p.parts:
                        continue
                    if p.is_absolute() or ".." in p.parts or "\\" in name or name in seen:
                        LOGGER.error(
                            "rag_kb_load_failed reason=unsafe_or_duplicate_zip_member source=%s",
                            name,
                        )
                        raise ValueError(f"unsafe or duplicate ZIP member: {name}")
                    seen.add(name)
                    if total + info.file_size > limit:
                        LOGGER.error(
                            "rag_kb_load_failed reason=byte_limit_exceeded source=%s "
                            "limit_bytes=%d",
                            name, limit,
                        )
                        raise ValueError("knowledge base exceeds byte limit")
                    try:
                        # Read directly; never extract archive paths to the filesystem.
                        raw = z.read(info)
                        if total + len(raw) > limit:
                            LOGGER.error(
                                "rag_kb_load_failed reason=byte_limit_exceeded source=%s "
                                "limit_bytes=%d",
                                name, limit,
                            )
                            raise ValueError("knowledge base exceeds byte limit")
                        text = raw.decode("utf-8-sig")
                    except UnicodeError as exc:
                        LOGGER.error(
                            "rag_document_read_failed source=%s reason=invalid_utf8 error_type=%s",
                            name, type(exc).__name__,
                        )
                        raise ValueError(
                            f"cannot decode knowledge document '{name}' as UTF-8"
                        ) from exc
                    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                        LOGGER.error(
                            "rag_document_read_failed source=%s reason=archive_member_read_error "
                            "error_type=%s",
                            name, type(exc).__name__,
                        )
                        raise OSError(f"cannot read knowledge document '{name}' from archive") from exc
                    total += len(raw)
                    docs.append((name, text))
        except zipfile.BadZipFile as exc:
            LOGGER.error(
                "rag_kb_load_failed reason=invalid_zip path=%s error_type=%s",
                path, type(exc).__name__,
            )
            raise ValueError(f"knowledge-base ZIP archive is corrupted: {path}") from exc
    else:
        LOGGER.error("rag_kb_load_failed reason=unsupported_path path=%s", path)
        raise ValueError(f"expected knowledge-base directory or ZIP: {path}")
    docs = [(n, t) for n, t in docs if t.strip()]
    if not docs:
        LOGGER.error(
            "rag_kb_load_failed reason=no_nonempty_documents path=%s input_bytes=%d",
            path, total,
        )
        raise ValueError("knowledge base has no nonempty Markdown/TXT documents")
    return docs, total


def _chunks(source, text, cfg):
    lines = text.splitlines(keepends=True)
    title = Path(source).stem
    headings = []
    section_start = 0
    sections = []
    fence = False
    for i, line in enumerate(lines):
        if line.lstrip().startswith(("```", "~~~")):
            fence = not fence
        m = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line) if not fence else None
        if m:
            if i > section_start:
                sections.append((section_start, i, " / ".join(h for _, h in headings)))
            level, name = len(m[1]), m[2]
            if level == 1:
                title = name
            headings = [(l, h) for l, h in headings if l < level] + [(level, name)]
            section_start = i
    sections.append((section_start, len(lines), " / ".join(h for _, h in headings)))
    out = []
    for start, end, section in sections:
        # Keep short introductions with their child sections, not as empty context.
        first = re.match(r"^(#{1,6})\s", lines[start]) if start < len(lines) else None
        following = re.match(r"^(#{1,6})\s", lines[end]) if end < len(lines) else None
        if first and following and len(following[1]) > len(first[1]) and len("".join(lines[start:end])) < 250:
            extended = end
            while extended < len(lines):
                heading = re.match(r"^(#{1,6})\s", lines[extended])
                if heading and len(heading[1]) <= len(first[1]):
                    break
                if len("".join(lines[start:extended+1])) > cfg.chunk_chars:
                    break
                extended += 1
            end = extended
        pos = start
        while pos < end:
            stop = pos
            size = 0
            while stop < end and (size + len(lines[stop]) <= cfg.chunk_chars or stop == pos):
                size += len(lines[stop])
                stop += 1
            # Prefer a paragraph boundary, retaining every source line.
            if stop < end:
                breaks = [j + 1 for j in range(pos, stop) if not lines[j].strip()]
                if breaks and breaks[-1] > pos + (stop - pos) // 2:
                    stop = breaks[-1]
            piece = "".join(lines[pos:stop]).strip()
            meaningful = [s for s in piece.splitlines() if s.strip() and not re.match(r"^\s*(?:#{1,6}\s|[-_*]{3,}\s*$)", s)]
            if meaningful:
                digest = hashlib.sha256(f"{source}\0{pos}\0{piece}".encode()).hexdigest()[:20]
                out.append(Chunk(digest, source, title, section, piece, pos + 1, stop))
            if stop == end:
                break
            nxt = stop
            overlap = 0
            while nxt > pos + 1 and overlap + len(lines[nxt - 1]) <= cfg.overlap_chars:
                nxt -= 1
                overlap += len(lines[nxt])
            pos = nxt
    return out


def _normalize(vector):
    v = [float(x) for x in vector]
    if not v or any(not math.isfinite(x) for x in v):
        raise ValueError("invalid embedding")
    norm = math.sqrt(sum(x*x for x in v))
    if norm == 0:
        raise ValueError("zero embedding")
    return tuple(x / norm for x in v)


class KnowledgeSearch:
    def __init__(self, knowledge_path, config=None):
        self.config = config or Config.from_env()
        self._lock = threading.Lock()
        self._query_cache = collections.OrderedDict()
        self._init_warnings = []
        started = time.perf_counter()
        stage = time.perf_counter()
        docs, input_bytes = _documents(knowledge_path, self.config.max_input_bytes)
        build_timing = {"load_ms": round((time.perf_counter()-stage)*1000, 3)}
        stage = time.perf_counter()
        self.chunks = tuple(c for n, t in docs for c in _chunks(n, t, self.config))
        build_timing["chunk_ms"] = round((time.perf_counter()-stage)*1000, 3)
        if not self.chunks:
            LOGGER.error(
                "rag_index_build_failed reason=no_searchable_chunks documents=%d input_bytes=%d",
                len(docs), input_bytes,
            )
            raise ValueError("knowledge base produced no searchable text fragments")
        stage = time.perf_counter()
        self._tf = []
        self._lengths = []
        self._postings = collections.defaultdict(list)
        for i, c in enumerate(self.chunks):
            terms = _terms(c.text) + _terms(c.section.split(" / ")[-1])
            tf = collections.Counter(terms)
            self._tf.append(tf)
            self._lengths.append(len(terms))
            for term in tf:
                self._postings[term].append(i)
        self._avg = sum(self._lengths) / max(1, len(self.chunks))
        raw_grams = [_grams(self._embedding_text(c)) for c in self.chunks]
        df = collections.Counter(g for counts in raw_grams for g in counts)
        self._gram_idf = {g: math.log((1+len(self.chunks))/(1+n))+1 for g,n in df.items()}
        self._char_vectors = tuple(self._char_vector(counts) for counts in raw_grams)
        self._labels = tuple(set(_terms(c.section.split(" / ")[-1])) for c in self.chunks)
        self._titles = tuple(" ".join(_terms(c.title)) for c in self.chunks)
        self._prefixes = collections.defaultdict(list)
        for term in self._postings:
            if len(term) >= 4:
                self._prefixes[term[:4]].append(term)
        build_timing["local_index_ms"] = round((time.perf_counter()-stage)*1000, 3)
        self._vectors = None
        cfg = self.config
        stage = time.perf_counter()
        if cfg.mode != "local" and cfg.base_url and cfg.api_key:
            try:
                vectors = []
                for i in range(0, len(self.chunks), cfg.batch_size):
                    vectors.extend(self._embed([self._embedding_text(c) for c in self.chunks[i:i+cfg.batch_size]]))
                if len({len(v) for v in vectors}) != 1:
                    raise ValueError("inconsistent embedding dimensions")
                self._vectors = tuple(vectors)
            except Exception as exc:
                self._init_warnings.extend([
                    "embedding_index_unavailable:" + type(exc).__name__,
                    "fallback_local:embedding_index_unavailable",
                ])
                LOGGER.warning(
                    "rag_embedding_index_failed error_type=%s fallback=local",
                    type(exc).__name__,
                )
        elif cfg.mode != "local":
            missing = ",".join(
                name for name, value in (("base_url", cfg.base_url), ("api_key", cfg.api_key))
                if not value
            )
            self._init_warnings.extend([
                "embedding_credentials_missing",
                "fallback_local:embedding_credentials_missing",
            ])
            LOGGER.warning(
                "rag_embedding_configuration_incomplete missing=%s fallback=local", missing,
            )
        build_timing["embeddings_ms"] = round((time.perf_counter()-stage)*1000, 3)
        build_timing["total_ms"] = round((time.perf_counter()-started)*1000, 3)
        self.info = {
            "documents": len(docs), "chunks": len(self.chunks),
            "input_bytes": input_bytes,
            "embedding_index_ready": self._vectors is not None,
            "effective_mode": "hybrid" if self._vectors is not None else "local",
            "warnings": list(self._init_warnings),
            "build_ms": build_timing["total_ms"], "timing_ms": build_timing,
        }
        LOGGER.info(
            "rag_index_ready documents=%d chunks=%d input_bytes=%d mode=%s "
            "embedding_index_ready=%s load_ms=%.3f chunk_ms=%.3f "
            "local_index_ms=%.3f embeddings_ms=%.3f total_ms=%.3f",
            self.info["documents"], self.info["chunks"], self.info["input_bytes"],
            self.info["effective_mode"], self.info["embedding_index_ready"],
            build_timing["load_ms"], build_timing["chunk_ms"],
            build_timing["local_index_ms"], build_timing["embeddings_ms"],
            build_timing["total_ms"],
        )

    @staticmethod
    def _embedding_text(c):
        return c.title + "\n" + c.section + "\n" + c.text

    def _char_vector(self, counts):
        values = {g: (1+math.log(n))*self._gram_idf[g] for g,n in counts.items() if g in self._gram_idf}
        norm = math.sqrt(sum(v*v for v in values.values())) or 1
        return {g:v/norm for g,v in values.items()}

    def _post(self, endpoint, body):
        cfg = self.config
        url = cfg.base_url.rstrip("/") + endpoint
        if not url.startswith(("http://", "https://")):
            raise ValueError("invalid API URL")
        payload = json.dumps(body).encode()
        for attempt in range(cfg.retries + 1):
            req = urllib.request.Request(url, data=payload, headers={
                "Content-Type": "application/json", "Authorization": "Bearer " + cfg.api_key})
            try:
                with urllib.request.urlopen(req, timeout=cfg.timeout) as r:
                    raw = r.read(16_000_001)
                    if len(raw) > 16_000_000:
                        raise ValueError("API response exceeds limit")
                    return json.loads(raw)
            except urllib.error.HTTPError as exc:
                retry = exc.code in {429, 500, 502, 503, 504} and attempt < cfg.retries
                exc.close()
                if not retry:
                    raise
            except (urllib.error.URLError, TimeoutError):
                if attempt == cfg.retries:
                    raise
            time.sleep(0.25 * (2 ** attempt))

    def _embed(self, texts):
        result = self._post("/embeddings", {"model": self.config.embedding_model, "input": texts})
        items = result["data"]
        if len(items) != len(texts):
            raise ValueError("missing embedding results")
        ordered = {}
        for i, item in enumerate(items):
            idx = int(item.get("index", i))
            if idx in ordered or not 0 <= idx < len(texts):
                raise ValueError("invalid embedding index")
            ordered[idx] = _normalize(item["embedding"])
        return [ordered[i] for i in range(len(texts))]

    def _lexical(self, query):
        normalized = re.sub(r"[^a-zа-я0-9 ]", " ", query.lower().replace("ё", "е"))
        normalized = " ".join(normalized.split())
        expansion = " ".join(v for p,v in PHRASES.items() if p in normalized)
        original = set(_terms(query))
        weights = {t: 1.0 for t in original}
        for t in _terms(expansion):
            weights.setdefault(t, 0.65)
        for t in original:
            for root, related in SYNONYMS.items():
                if t == root or min(len(t), len(root)) >= 4 and (t.startswith(root) or root.startswith(t)):
                    for other in related:
                        weights.setdefault(other, 0.5)
        for root, weight in list(weights.items()):
            if len(root) >= 4:
                for term in self._prefixes.get(root[:4], ()):
                    if term.startswith(root) or root.startswith(term):
                        weights.setdefault(term, weight * 0.75)
        scores = collections.defaultdict(float)
        n = len(self.chunks)
        for t, weight in weights.items():
            postings = self._postings.get(t, ())
            idf = math.log(1 + (n - len(postings) + 0.5) / (len(postings) + 0.5))
            for i in postings:
                f = self._tf[i][t]
                scores[i] += weight * idf * f * 2.2 / (f + 1.2 * (0.25 + 0.75*self._lengths[i]/self._avg))
        hinted_titles = [label for label,needles in TOPICS if any(t in normalized for t in needles)]
        for i in list(scores):
            scores[i] += 1.15 * len(original & self._labels[i])
            if any(all(t in self._titles[i] for t in label.split()) for label in hinted_titles):
                scores[i] += 3.4
        return sorted(scores, key=lambda i: (-scores[i], i)), scores

    @staticmethod
    def _dialog_query(question, history):
        # Accept either the platform's single text field or structured chat history.
        if history:
            if not isinstance(history, (list, tuple)):
                raise TypeError("history must be a list of role/content messages")
            parts = []
            for m in history[-8:]:
                if not isinstance(m, dict) or not isinstance(m.get("content"), str):
                    raise TypeError("invalid history message")
                if m.get("role") == "user":
                    parts.append(m["content"][-2000:])
            return " ".join(parts[-3:] + [question, question])
        parts = re.split(r"(?:^|\n)\s*Пользователь:\s*", question, flags=re.I)
        if len(parts) > 1:
            users = [re.split(r"\n\s*Ассистент:", p, flags=re.I)[0] for p in parts[1:]]
            return " ".join(users[-3:] + [users[-1], users[-1]])
        return question

    def search(self, question, top_k=None, history=None):
        started = time.perf_counter()
        timing = {"local_ms": 0.0, "embeddings_ms": 0.0, "fusion_ms": 0.0,
                  "reranker_ms": 0.0, "selection_ms": 0.0}
        if not isinstance(question, str):
            raise TypeError("question must be a string")
        k = self.config.top_k if top_k is None else top_k
        if not isinstance(k, int) or isinstance(k, bool) or k < 1:
            raise ValueError("top_k must be a positive integer")
        if len(question) > 50000:
            raise ValueError("question exceeds 50000 characters")
        warnings = list(self._init_warnings)
        query = self._dialog_query(question.strip(), history)
        lexical, scores = self._lexical(query) if question.strip() else ([], {})
        ranks = [(lexical, 1.0)]
        # A second view focuses on the latest request, without erasing history.
        last_user = re.split(r"(?:^|\n)\s*Пользователь:\s*", question, flags=re.I)[-1]
        last_user = re.split(r"\n\s*Ассистент:", last_user, flags=re.I)[0]
        if history or last_user != question:
            recent, _ = self._lexical(last_user)
            ranks.append((recent, 0.8))
        if lexical:
            qchars = self._char_vector(_grams(query))
            char_scores = [sum(value*v.get(g,0) for g,value in qchars.items()) for v in self._char_vectors]
            char_ranking = sorted(
                (i for i, score in enumerate(char_scores) if score > 0),
                key=lambda i: (-char_scores[i], i),
            )
            ranks.append((char_ranking, 0.35))
        timing["local_ms"] = round((time.perf_counter()-started)*1000, 3)
        mode = "local"
        if self._vectors is not None and question.strip():
            stage = time.perf_counter()
            try:
                with self._lock:
                    qv = self._query_cache.get(query)
                if qv is None:
                    qv = self._embed([query])[0]
                    with self._lock:
                        self._query_cache[query] = qv
                        if len(self._query_cache) > 128:
                            self._query_cache.popitem(last=False)
                if len(qv) != len(self._vectors[0]):
                    raise ValueError("query embedding dimension mismatch")
                sims = [sum(a*b for a,b in zip(qv,v)) for v in self._vectors]
                dense = sorted(range(len(sims)), key=lambda i: (-sims[i], i))
                ranks.append((dense, 1.0))
                mode = "hybrid"
            except Exception as exc:
                warnings.extend([
                    "query_embedding_unavailable:" + type(exc).__name__,
                    "fallback_local:query_embedding_unavailable",
                ])
                LOGGER.warning(
                    "rag_query_embedding_failed error_type=%s fallback=local",
                    type(exc).__name__,
                )
            timing["embeddings_ms"] = round((time.perf_counter()-stage)*1000, 3)
        stage = time.perf_counter()
        fused = collections.defaultdict(float)
        pool = max(k, self.config.candidates)
        for ranking, weight in ranks:
            for rank, idx in enumerate(ranking[:pool], 1):
                fused[idx] += weight / (60 + rank)
        max_lexical = max(scores.values(), default=1) or 1
        for idx in lexical[:pool]:
            fused[idx] += 0.02 * scores[idx] / max_lexical
        order = sorted(fused, key=lambda i: (-fused[i], i))[:pool]
        timing["fusion_ms"] = round((time.perf_counter()-stage)*1000, 3)
        reranked = False
        cfg = self.config
        if order and cfg.mode != "local" and cfg.reranker_model and cfg.base_url and cfg.api_key:
            stage = time.perf_counter()
            try:
                data = self._post("/rerank", {"model": cfg.reranker_model, "query": query,
                    "documents": [self._embedding_text(self.chunks[i]) for i in order], "top_n": min(cfg.reranker_top_n, len(order))})
                entries = data.get("results", data.get("data"))
                indices = [int(e["index"]) for e in entries]
                if not indices or len(indices) != len(set(indices)) or any(i < 0 or i >= len(order) for i in indices):
                    raise ValueError("invalid reranker result")
                order = [order[i] for i in indices] + [v for i,v in enumerate(order) if i not in indices]
                reranked = True
            except Exception as exc:
                warnings.extend([
                    "reranker_unavailable:" + type(exc).__name__,
                    "fallback_candidates:reranker_unavailable",
                ])
                LOGGER.warning(
                    "rag_reranker_failed error_type=%s fallback=candidate_order",
                    type(exc).__name__,
                )
            timing["reranker_ms"] = round((time.perf_counter()-stage)*1000, 3)
        stage = time.perf_counter()
        selected = []
        for idx in order:
            c = self.chunks[idx]
            if any(c.source == p.source and len(set(range(c.line_start, c.line_end+1)) & set(range(p.line_start,p.line_end+1))) / max(1,min(c.line_end-c.line_start+1,p.line_end-p.line_start+1)) > 0.75 for _,p in selected):
                continue
            selected.append((idx, c))
            if len(selected) == k:
                break
        hits = [dict(asdict(c), rank=i+1, score=round(fused[idx], 7),
                     lexical_score=round(scores.get(idx, 0), 5)) for i,(idx,c) in enumerate(selected)]
        timing["selection_ms"] = round((time.perf_counter()-stage)*1000, 3)
        timing["total_ms"] = round((time.perf_counter()-started)*1000, 3)
        LOGGER.info(
            "rag_search_complete query_chars=%d top_k=%d hits=%d sources=%d mode=%s "
            "reranked=%s warnings=%d local_ms=%.3f embeddings_ms=%.3f "
            "fusion_ms=%.3f reranker_ms=%.3f selection_ms=%.3f total_ms=%.3f",
            len(question), k, len(hits), len({hit["source"] for hit in hits}), mode,
            reranked, len(warnings), timing["local_ms"], timing["embeddings_ms"],
            timing["fusion_ms"], timing["reranker_ms"], timing["selection_ms"],
            timing["total_ms"],
        )
        return {"schema_version": 1, "query": question,
                "hits": hits, "sources": list(dict.fromkeys(h["source"] for h in hits)),
                "search_ms": timing["total_ms"], "timing_ms": timing,
                "mode": mode, "reranked": reranked, "warnings": warnings,
                "needs_review": not hits,
                "score_kind": "rrf_with_normalized_lexical_not_probability"}


def main():
    logging.basicConfig(
        level=getattr(logging, _env("RAG_LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    parser = argparse.ArgumentParser(description="Retrieve knowledge fragments; never generate answers")
    parser.add_argument("--kb", required=True)
    parser.add_argument("--question")
    parser.add_argument("--questions", help="CSV with id,question; output JSONL to stdout")
    args = parser.parse_args()
    engine = KnowledgeSearch(args.kb)
    if bool(args.question) == bool(args.questions):
        parser.error("provide exactly one of --question and --questions")
    if args.questions:
        with open(args.questions, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                print(json.dumps({"id": row["id"], **engine.search(row["question"])}, ensure_ascii=False))
    else:
        print(json.dumps(engine.search(args.question), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
