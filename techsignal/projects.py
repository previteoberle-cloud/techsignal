"""R6: research projects that a non-programmer creates in the workbench.

The user types plain text: a subject, a research question, the current judgement, key assumptions,
public URLs to watch and optional keywords / change signals. This module turns that into the same
Thesis and SourceProfile records the pipeline already reads. Nobody writes JSON, Python or regex:
every phrase the user types is matched literally (re.escape, case-insensitive). That is plain
keyword matching, not semantic understanding, and the workbench says so.

Storage (one directory per project, outside the committed demo cases):

    <projects root>/                    TECHSIGNAL_PROJECTS_ROOT, default ~/.techsignal/projects
      p-<12 hex>/
        project.json                    current spec + status (draft/ready) + version
        versions/v<N>.json              every saved version, never rewritten (edit history)
        theses.json, sources.json       generated from the current version when status is ready
        monitor.json, checks.jsonl,     monitoring state (techsignal.project_monitor); never touches
        state/, runs/                   project.json - background work cannot rewrite the judgement

Writes are atomic (temp file + os.replace). Edits use optimistic concurrency: a save names the
version it was based on and is refused if somebody saved in between.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import secrets
import tempfile
import time
import unicodedata
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .models import Assumption, EvidenceGrade, SourceProfile, SourceType, Thesis

PROJECTS_ENV = "TECHSIGNAL_PROJECTS_ROOT"
ALLOW_PRIVATE_ENV = "TECHSIGNAL_ALLOW_PRIVATE_URLS"
PROJECT_ID_RE = re.compile(r"^p-[0-9a-f]{12}$")
SCHEMA = "techsignal.project/1"

# Limits keep the generated rules small and the pages readable.
MAX_NAME, MAX_ALIASES, MAX_QUESTION, MAX_JUDGEMENT = 60, 8, 500, 2000
MAX_ASSUMPTIONS, MAX_STATEMENT = 8, 300
MAX_PHRASES, MIN_PHRASE, MAX_PHRASE = 12, 2, 60
MAX_KEYWORDS = 20
MAX_SOURCES, MAX_URL, MAX_LABEL = 10, 500, 60

# Grade follows the source type the user declares. A user-declared "official" source is grade A:
# the declaration is the user's, and the source profile note says so.
GRADE_FOR_TYPE = {
    SourceType.OFFICIAL: EvidenceGrade.A, SourceType.REGULATOR: EvidenceGrade.B, SourceType.MEDIA: EvidenceGrade.B,
    SourceType.ANALYST: EvidenceGrade.C, SourceType.FORUM: EvidenceGrade.D, SourceType.UNKNOWN: EvidenceGrade.D,
}
SOURCE_TYPE_CHOICES = [SourceType.OFFICIAL, SourceType.REGULATOR, SourceType.MEDIA, SourceType.ANALYST,
                       SourceType.FORUM, SourceType.UNKNOWN]


class ProjectError(ValueError):
    """A problem the user should read, in Chinese. `problems` lists every field problem found."""

    def __init__(self, message: str, problems: list[str] | None = None):
        super().__init__(message)
        self.problems = problems or [message]


class ProjectConflict(ProjectError):
    pass


class ProjectNotFound(ProjectError):
    pass


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AssumptionInput(_Strict):
    statement: str = ""
    support_signals: list[str] = Field(default_factory=list)   # phrases that would support it
    weaken_signals: list[str] = Field(default_factory=list)    # phrases that would weaken it


class SourceInput(_Strict):
    url: str
    label: str = ""
    source_type: SourceType = SourceType.OFFICIAL


class ProjectSpec(_Strict):
    name: str = ""                     # company or subject
    aliases: list[str] = Field(default_factory=list)
    question: str = ""                 # technical field / core research question
    judgement: str = ""                # the current research judgement, in the user's words
    assumptions: list[AssumptionInput] = Field(default_factory=list)
    sources: list[SourceInput] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    owner: str = "研究负责人"


class Project(_Strict):
    schema_: Literal["techsignal.project/1"] = Field(SCHEMA, alias="schema")
    project_id: str
    status: Literal["draft", "ready"]
    version: int
    created_at: str
    updated_at: str
    spec: ProjectSpec

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @property
    def title(self) -> str:
        return self.spec.name or "（未命名草稿）"

    @property
    def thesis_id(self) -> str:
        return thesis_id_for(self.project_id)


class VersionRecord(_Strict):
    version: int
    saved_at: str
    status: Literal["draft", "ready"]
    note: str = ""
    spec: ProjectSpec


def thesis_id_for(project_id: str) -> str:
    return f"TH-{project_id}"


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Text and URL checks
# ---------------------------------------------------------------------------

_SPLIT_RE = re.compile(r"[\n,，、;；]+")


def clean_text(value: str) -> str:
    """Collapse whitespace; drop control and format characters (zero-width, bidi overrides)."""
    value = "".join(ch for ch in str(value or "") if ch in "\n\t" or unicodedata.category(ch)[0] != "C")
    return re.sub(r"[ \t　]+", " ", value).strip()


def split_items(text: str) -> list[str]:
    """'a, b，c、d' or one per line -> ['a', 'b', 'c', 'd'] (deduplicated, order kept)."""
    out: list[str] = []
    for part in _SPLIT_RE.split(text or ""):
        p = clean_text(part)
        if p and p.casefold() not in {x.casefold() for x in out}:
            out.append(p)
    return out


def name_key(name: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", clean_text(name))).casefold()


_BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home", ".corp", ".intranet", ".arpa")


def check_public_url(url: str, allow_private: bool = False) -> str:
    """The normalized URL, or ProjectError saying in Chinese why it cannot be watched.

    Only http(s) URLs of public hosts are accepted: no credentials in the URL, no localhost or
    private/reserved IP addresses, no single-label or internal host names. (A public name can still
    resolve to a private address; changedetection.io refuses to fetch those by default.)

    allow_private=True is a development/testing opt-in (TECHSIGNAL_ALLOW_PRIVATE_URLS=1, mirroring
    changedetection.io's own ALLOW_IANA_RESTRICTED_ADDRESSES). Scheme and credential checks still apply."""
    raw = clean_text(url)
    if not raw:
        raise ProjectError("网址为空")
    if len(raw) > MAX_URL:
        raise ProjectError(f"网址过长（超过 {MAX_URL} 个字符）")
    if any(ch.isspace() for ch in raw):
        raise ProjectError(f"网址中不能有空格：{raw}")
    parts = urlsplit(raw)
    if parts.scheme.lower() not in ("http", "https"):
        raise ProjectError(f"只支持 http:// 或 https:// 开头的公开网址：{raw}")
    if parts.username or parts.password or "@" in parts.netloc:
        raise ProjectError(f"网址中不能包含用户名或密码：{raw}")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise ProjectError(f"网址缺少域名：{raw}")
    try:
        port = parts.port
    except ValueError:
        raise ProjectError(f"网址端口无效：{raw}") from None
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if allow_private:
        pass
    elif ip is not None:
        if not ip.is_global:
            raise ProjectError(f"不能监测本机、内网或保留地址：{raw}")
    else:
        if host == "localhost" or host.endswith(_BLOCKED_SUFFIXES):
            raise ProjectError(f"不能监测本机或内网地址：{raw}")
        if "." not in host:
            raise ProjectError(f"请填写完整的公开域名（例如 www.example.com）：{raw}")
        try:
            ascii_host = host.encode("idna").decode("ascii")
        except UnicodeError:
            raise ProjectError(f"域名格式不正确：{raw}") from None
        if not re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", ascii_host):
            raise ProjectError(f"域名格式不正确：{raw}")
    netloc = host if port is None else f"{host}:{port}"
    if ip is not None and ip.version == 6:
        netloc = f"[{host}]" if port is None else f"[{host}]:{port}"
    return urlunsplit((parts.scheme.lower(), netloc, parts.path or "/", parts.query, ""))


def _url_key(url: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, p.path.rstrip("/") or "/", p.query, ""))


def _phrases(items: list[str], what: str, problems: list[str], limit: int = MAX_PHRASES) -> list[str]:
    out: list[str] = []
    for raw in items:
        p = clean_text(raw)
        if not p:
            continue
        if len(p) < MIN_PHRASE:
            problems.append(f"{what}「{p}」太短（至少 {MIN_PHRASE} 个字符），容易误命中")
            continue
        if len(p) > MAX_PHRASE:
            problems.append(f"{what}「{p[:20]}…」太长（最多 {MAX_PHRASE} 个字符）")
            continue
        if p.casefold() not in {x.casefold() for x in out}:
            out.append(p)
    if len(out) > limit:
        problems.append(f"{what}最多 {limit} 个，当前 {len(out)} 个")
    return out


def _bounded(value: str, limit: int, what: str, problems: list[str]) -> str:
    v = clean_text(value)
    if len(v) > limit:
        problems.append(f"{what}过长（最多 {limit} 个字符，当前 {len(v)} 个）")
    return v


def check_spec(spec: ProjectSpec, ready: bool, allow_private: bool = False) -> ProjectSpec:
    """Cleaned spec, or ProjectError listing every problem. Drafts may be incomplete, but whatever
    they contain must still be valid (a draft cannot hold a private URL either)."""
    problems: list[str] = []
    name = _bounded(spec.name, MAX_NAME, "研究对象名称", problems)
    aliases = [a for a in _phrases(spec.aliases, "别名", problems, MAX_ALIASES) if name_key(a) != name_key(name)]
    question = _bounded(spec.question, MAX_QUESTION, "研究问题", problems)
    judgement = _bounded(spec.judgement, MAX_JUDGEMENT, "当前研究判断", problems)
    owner = _bounded(spec.owner, 40, "负责人", problems) or "研究负责人"
    assumptions = []
    for i, a in enumerate(spec.assumptions, 1):
        st = _bounded(a.statement, MAX_STATEMENT, f"假设 {i}", problems)
        sup = _phrases(a.support_signals, f"假设 {i} 的支持信号", problems)
        wk = _phrases(a.weaken_signals, f"假设 {i} 的削弱信号", problems)
        both = {x.casefold() for x in sup} & {x.casefold() for x in wk}
        if both:
            problems.append(f"假设 {i} 中「{'、'.join(sorted(both))}」同时被写成支持和削弱信号")
        if not st and (sup or wk):
            problems.append(f"假设 {i} 填写了信号词，但缺少假设内容")
        if st:
            assumptions.append(AssumptionInput(statement=st, support_signals=sup, weaken_signals=wk))
    if len(assumptions) > MAX_ASSUMPTIONS:
        problems.append(f"关键假设最多 {MAX_ASSUMPTIONS} 条")
    keywords = _phrases(spec.keywords, "关键词", problems, MAX_KEYWORDS)
    sources, seen = [], set()
    for s in spec.sources:
        if not clean_text(s.url):
            continue
        try:
            url = check_public_url(s.url, allow_private=allow_private)
        except ProjectError as e:
            problems.append(str(e))
            continue
        if _url_key(url) in seen:
            continue
        seen.add(_url_key(url))
        sources.append(SourceInput(url=url, label=_bounded(s.label, MAX_LABEL, "来源说明", problems),
                                   source_type=s.source_type))
    if len(sources) > MAX_SOURCES:
        problems.append(f"监测网址最多 {MAX_SOURCES} 个，当前 {len(sources)} 个")
    if ready:
        if not name:
            problems.append("请填写研究对象（公司或主题名称）")
        elif len(name) < 2:
            problems.append("研究对象名称至少 2 个字符")
        if not question:
            problems.append("请填写技术领域或核心研究问题")
        if not judgement:
            problems.append("请填写当前研究判断")
        if not assumptions:
            problems.append("请至少填写一条关键假设")
        if not sources:
            problems.append("请至少填写一个要监测的公开网址")
    elif not name:
        problems.append("草稿也需要先填写研究对象名称，便于之后找到它")
    if problems:
        raise ProjectError(f"有 {len(problems)} 处需要修改", problems)
    return ProjectSpec(name=name, aliases=aliases, question=question, judgement=judgement, assumptions=assumptions,
                       sources=sources, keywords=keywords, owner=owner)


def spec_from_form(name: str = "", aliases: str = "", question: str = "", judgement: str = "",
                   assumptions: list[dict] | None = None, urls: str = "", source_types: dict | None = None,
                   keywords: str = "", owner: str = "") -> ProjectSpec:
    """Plain form text -> ProjectSpec (not yet checked). assumptions: [{statement, support, weaken}]
    with comma/newline separated phrases; urls: one per line, optionally "网址 说明"."""
    source_types = source_types or {}
    srcs = []
    for line in (urls or "").splitlines():
        line = line.strip()
        if not line:
            continue
        url, _, label = line.partition(" ")
        srcs.append(SourceInput(url=url, label=label.strip(),
                                source_type=source_types.get(url, SourceType.OFFICIAL)))
    return ProjectSpec(
        name=name or "", aliases=split_items(aliases), question=question or "", judgement=judgement or "",
        assumptions=[AssumptionInput(statement=a.get("statement", "") or "",
                                     support_signals=split_items(a.get("support", "")),
                                     weaken_signals=split_items(a.get("weaken", "")))
                     for a in (assumptions or [])],
        sources=srcs, keywords=split_items(keywords), owner=owner or "研究负责人")


# ---------------------------------------------------------------------------
# Spec -> pipeline inputs (deterministic, no model)
# ---------------------------------------------------------------------------

def literal_pattern(phrase: str) -> str:
    """A user phrase as a case-insensitive literal pattern. No regex syntax reaches the rules.
    A phrase that starts/ends with a Latin letter or digit must not be glued to another one, so
    "70B" does not fire on "170B" and "Llama" does not fire on "Llamas"."""
    pat = re.escape(phrase)
    if re.match(r"[A-Za-z0-9]", phrase):
        pat = r"(?<![A-Za-z0-9])" + pat
    if re.search(r"[A-Za-z0-9]$", phrase):
        pat += r"(?![A-Za-z0-9])"
    return "(?i)" + pat


def build_thesis(project: Project) -> Thesis:
    s = project.spec
    return Thesis(
        thesis_id=project.thesis_id,
        title=f"{s.name}：{s.question}"[:200],
        conclusion=s.judgement,
        entities=[s.name] + s.aliases,
        topic_keywords=list(s.keywords),
        assumptions=[Assumption(assumption_id=f"A{i}", statement=a.statement,
                                support_patterns=[literal_pattern(p) for p in a.support_signals],
                                weaken_patterns=[literal_pattern(p) for p in a.weaken_signals])
                     for i, a in enumerate(s.assumptions, 1)],
        status="active", as_of=project.updated_at[:10], owner=s.owner, fictional=False, sources=[])


def build_sources(project: Project) -> list[SourceProfile]:
    out: dict[tuple[str, str], SourceProfile] = {}
    for s in project.spec.sources:
        p = urlsplit(s.url)
        prefix = "" if p.path in ("", "/") else p.path
        out[(p.hostname or "", prefix)] = SourceProfile(
            domain=p.hostname or "", path_prefix=prefix, name=s.label or (p.hostname or "") + prefix,
            source_type=s.source_type, grade=GRADE_FOR_TYPE[s.source_type],
            note="来源类型由项目创建者声明，等级按类型确定")
    return list(out.values())


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

def default_projects_root() -> Path:
    env = os.getenv(PROJECTS_ENV)
    return Path(env).expanduser() if env else Path.home() / ".techsignal" / "projects"


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _dump(model: BaseModel) -> str:
    return json.dumps(model.model_dump(mode="json", by_alias=True), ensure_ascii=False, indent=2) + "\n"


@contextmanager
def file_lock(path: Path, timeout: float = 10.0, stale: float = 120.0):
    """Cross-process lock: mkdir is atomic everywhere. A lock older than `stale` seconds is taken over
    (its holder crashed)."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            path.mkdir(parents=True)
            break
        except FileExistsError:
            try:
                if time.time() - path.stat().st_mtime > stale:
                    path.rmdir()
                    continue
            except OSError:
                pass
            if time.monotonic() > deadline:
                raise ProjectError("项目正被另一个操作占用，请稍后重试") from None
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            path.rmdir()
        except OSError:
            pass


class ProjectStore:
    def __init__(self, root: Path | None = None, allow_private_urls: bool | None = None):
        self.root = Path(root) if root is not None else default_projects_root()
        # Development/testing opt-in only; the workbench shows a warning banner while it is on.
        self.allow_private_urls = (os.getenv(ALLOW_PRIVATE_ENV) == "1") if allow_private_urls is None \
            else bool(allow_private_urls)
        self._guard_root()

    def _guard_root(self) -> None:
        """Projects never live inside the committed demo cases."""
        from .upstream import PROJECT_ROOT
        root = self.root.expanduser().resolve()
        committed = [(PROJECT_ROOT / "cases").resolve(), (PROJECT_ROOT / "demo").resolve()]
        if os.getenv("TECHSIGNAL_CASES_ROOT"):
            committed.append(Path(os.environ["TECHSIGNAL_CASES_ROOT"]).expanduser().resolve())
        for c in committed:
            if root == c or c in root.parents:
                raise ProjectError(f"研究项目目录不能放在示例案例目录中：{root}")

    # -- paths -------------------------------------------------------------------------------

    def project_dir(self, project_id: str) -> Path:
        if not isinstance(project_id, str) or not PROJECT_ID_RE.fullmatch(project_id):
            raise ProjectNotFound("项目编号无效")
        root = self.root.resolve()
        d = (root / project_id).resolve()
        if d.parent != root:
            raise ProjectNotFound("项目编号无效")
        return d

    def theses_path(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "theses.json"

    def sources_path(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "sources.json"

    def _lock(self, project_id: str | None = None):
        target = self.project_dir(project_id) if project_id else self.root
        return file_lock(target / ".lock")

    # -- reading -----------------------------------------------------------------------------

    def get(self, project_id: str) -> Project:
        d = self.project_dir(project_id)
        f = d / "project.json"
        if not f.exists():
            raise ProjectNotFound("项目不存在或已被移走")
        try:
            p = Project.model_validate_json(f.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as e:
            raise ProjectError(f"项目文件损坏，无法读取（{d.name}）：{e}") from None
        if p.project_id != project_id:
            raise ProjectError(f"项目文件与目录不一致：{d.name}")
        return p

    def list(self) -> tuple[list[Project], list[str]]:
        """Readable projects (newest first) and problems with unreadable ones."""
        if not self.root.exists():
            return [], []
        out, problems = [], []
        for d in sorted(self.root.iterdir()):
            if not d.is_dir() or not PROJECT_ID_RE.fullmatch(d.name):
                continue
            try:
                out.append(self.get(d.name))
            except ProjectError as e:
                problems.append(str(e))
        out.sort(key=lambda p: p.updated_at, reverse=True)
        return out, problems

    def versions(self, project_id: str) -> list[VersionRecord]:
        d = self.project_dir(project_id) / "versions"
        out = []
        for f in d.glob("v*.json"):
            try:
                out.append(VersionRecord.model_validate_json(f.read_text(encoding="utf-8")))
            except (OSError, ValidationError):
                continue
        return sorted(out, key=lambda v: v.version)

    def version(self, project_id: str, n: int) -> VersionRecord:
        f = self.project_dir(project_id) / "versions" / f"v{int(n)}.json"
        if not f.exists():
            raise ProjectNotFound(f"版本 v{n} 不存在")
        return VersionRecord.model_validate_json(f.read_text(encoding="utf-8"))

    # -- writing -----------------------------------------------------------------------------

    def _check_name_free(self, name: str, except_id: str | None = None) -> None:
        key = name_key(name)
        for p in self.list()[0]:
            if p.project_id != except_id and name_key(p.spec.name) == key:
                raise ProjectError(f"已存在同名研究项目「{p.spec.name}」，请换一个名称或打开已有项目",
                                   [f"已存在同名研究项目「{p.spec.name}」"])

    def _write(self, project: Project, note: str) -> None:
        d = self.project_dir(project.project_id)
        rec = VersionRecord(version=project.version, saved_at=project.updated_at, status=project.status,
                            note=note, spec=project.spec)
        # Callers hold the project lock and took project.version from project.json, so an existing
        # file of that number can only be left over from a save that crashed before project.json was
        # replaced; it was never in force and is overwritten.
        vfile = d / "versions" / f"v{project.version}.json"
        # Generated pipeline inputs first, then the version record, then project.json: a crash
        # part-way leaves the previous project.json (and its version) in force.
        if project.status == "ready":
            thesis = build_thesis(project)
            atomic_write(d / "theses.json", json.dumps({"theses": [thesis.model_dump(mode="json")]},
                                                       ensure_ascii=False, indent=2) + "\n")
            atomic_write(d / "sources.json", json.dumps(
                {"sources": [s.model_dump(mode="json") for s in build_sources(project)]},
                ensure_ascii=False, indent=2) + "\n")
        atomic_write(vfile, _dump(rec))
        atomic_write(d / "project.json", _dump(project))

    def create(self, spec: ProjectSpec, ready: bool) -> Project:
        spec = check_spec(spec, ready, self.allow_private_urls)
        self.root.mkdir(parents=True, exist_ok=True)
        with self._lock():
            self._check_name_free(spec.name)
            for _ in range(5):
                pid = "p-" + secrets.token_hex(6)
                try:
                    (self.root / pid).mkdir()
                    break
                except FileExistsError:
                    continue
            else:
                raise ProjectError("无法分配项目编号，请重试")
            ts = now_iso()
            project = Project(project_id=pid, status="ready" if ready else "draft", version=1,
                              created_at=ts, updated_at=ts, spec=spec)
            try:
                self._write(project, "创建项目" if ready else "保存草稿")
            except BaseException:
                import shutil
                shutil.rmtree(self.root / pid, ignore_errors=True)
                raise
        return project

    def update(self, project_id: str, spec: ProjectSpec, expected_version: int, ready: bool,
               note: str = "") -> Project:
        """Save an edit. Refused (ProjectConflict) when the project changed since `expected_version`.
        A ready project cannot go back to draft: its runs refer to a complete judgement."""
        spec = check_spec(spec, ready, self.allow_private_urls)
        with self._lock():
            self._check_name_free(spec.name, except_id=project_id)
            with self._lock(project_id):
                cur = self.get(project_id)
                if cur.version != expected_version:
                    raise ProjectConflict(
                        f"项目已在别处被修改（你编辑的是 v{expected_version}，当前是 v{cur.version}）。"
                        "为避免覆盖，本次未保存；请重新打开项目后再修改。")
                if cur.status == "ready" and not ready:
                    raise ProjectError("已创建的项目不能退回草稿")
                if cur.spec == spec and cur.status == ("ready" if ready else "draft"):
                    return cur
                project = cur.model_copy(update={"spec": spec, "status": "ready" if ready else "draft",
                                                 "version": cur.version + 1, "updated_at": now_iso()})
                self._write(project, note or ("完成创建" if ready and cur.status == "draft" else "修改"))
        return project
