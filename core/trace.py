"""Agent 执行链路追踪 — 结构化 Trace / Span，用于可视化调试。

每个请求 = 一个 Trace，每个步骤 = 一个 Span，构成树状结构。
TraceStore 用环形缓冲区保留最近 N 条 Trace，供 API 查询和前端渲染。
"""

from __future__ import annotations

import time
import uuid
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator


# ---------------------------------------------------------------------------
#  Span — 单个执行步骤
# ---------------------------------------------------------------------------

@dataclass
class Span:
    """一个执行步骤的记录。"""

    span_id: str
    name: str                     # "intent_detection" | "semantic_search" 等
    parent_id: str | None = None  # 父 span，构成树
    start_time: float = 0.0
    end_time: float | None = None
    duration_ms: int | None = None

    # 输入输出
    input: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)

    # 元信息
    status: str = "running"       # "running" | "success" | "error"
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    # [新增] 子 span 列表，前端渲染树状结构
    children: list[Span] = field(default_factory=list)

    def finish(self, status: str = "success", error: str | None = None):
        """标记 span 完成，自动计算耗时。"""
        self.end_time = time.time()
        self.duration_ms = int((self.end_time - self.start_time) * 1000)
        self.status = status
        if error:
            self.error = error

    def to_dict(self) -> dict:
        """转为前端可渲染的 dict（递归包含子 span）。"""
        d: dict[str, Any] = {
            "span_id": self.span_id,
            "name": self.name,
            "parent_id": self.parent_id,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "error": self.error,
            "input_summary": _summarize(self.input),
            "output_summary": _summarize(self.output),
            "metadata": self.metadata,
            "children": [c.to_dict() for c in self.children],
        }
        # 完整 input / output 用于展开查看
        d["input"] = self.input
        d["output"] = self.output
        return d


def _summarize(data: dict) -> str:
    """将一个 dict 压缩为一行摘要字符串，供前端列表展示。"""
    if not data:
        return ""
    # 常见字段的简短摘要
    parts: list[str] = []
    for key in ("action", "source", "result_count", "tokens", "model"):
        if key in data:
            parts.append(f"{key}={data[key]}")
    if "filters" in data and isinstance(data["filters"], dict):
        f = data["filters"]
        active = [k for k, v in f.items() if v]
        if active:
            parts.append(f"filters=({','.join(active)})")
    if "message" in data:
        msg = str(data["message"])
        parts.append(f"msg={msg[:40]}{'...' if len(msg) > 40 else ''}")
    return ", ".join(parts) if parts else str(data)[:80]


# ---------------------------------------------------------------------------
#  Trace — 一次完整请求
# ---------------------------------------------------------------------------

@dataclass
class Trace:
    """一次完整请求的追踪记录。"""

    trace_id: str
    session_id: str
    user_message: str = ""
    start_time: float = 0.0
    end_time: float | None = None
    total_duration_ms: int | None = None

    spans: list[Span] = field(default_factory=list)
    _span_index: dict[str, Span] = field(default_factory=dict)

    # 汇总
    total_llm_calls: int = 0
    total_tokens: int = 0

    def add_span(self, span: Span):
        """注册一个 span（由 TraceContext 内部调用）。"""
        self.spans.append(span)
        self._span_index[span.span_id] = span

    def get_span(self, span_id: str) -> Span | None:
        return self._span_index.get(span_id)

    def finish(self):
        """标记整个 trace 完成。"""
        self.end_time = time.time()
        self.total_duration_ms = int((self.end_time - self.start_time) * 1000)
        # 汇总 LLM 调用次数
        self.total_llm_calls = sum(
            1 for s in self.spans if s.name in ("llm_generation", "profile_extraction", "intent_detection")
        )

    def to_dict(self) -> dict:
        """转为前端可渲染的完整 dict。"""
        # 构建 span 树：顶层 span（无 parent）按时间排序
        root_spans = [s for s in self.spans if s.parent_id is None]
        # 把有 parent 的 span 挂到父 span 的 children 里
        for s in self.spans:
            if s.parent_id:
                parent = self._span_index.get(s.parent_id)
                if parent and s not in parent.children:
                    parent.children.append(s)

        return {
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "user_message": self.user_message[:120],
            "start_time": self.start_time,
            "total_duration_ms": self.total_duration_ms,
            "total_llm_calls": self.total_llm_calls,
            "total_tokens": self.total_tokens,
            "spans": [s.to_dict() for s in root_spans],
        }

    def to_summary(self) -> dict:
        """轻量摘要，用于历史列表。"""
        return {
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "user_message": self.user_message[:80],
            "total_duration_ms": self.total_duration_ms,
            "total_llm_calls": self.total_llm_calls,
            "span_count": len(self.spans),
            "has_error": any(s.status == "error" for s in self.spans),
            "timestamp": self.start_time,
        }


# ---------------------------------------------------------------------------
#  TraceContext — 请求级别的上下文管理器
# ---------------------------------------------------------------------------

class TraceContext:
    """
    一次请求的追踪上下文。

    用法::

        trace = TraceContext(session_id, message)
        with trace.span("intent_detection") as span:
            intent = await detect_intent(...)
            span.output = {"action": intent["action"]}
        trace.finish()
        TraceStore.add(trace)
    """

    def __init__(self, session_id: str, user_message: str):
        self.trace = Trace(
            trace_id=f"tr_{uuid.uuid4().hex[:12]}",
            session_id=session_id,
            user_message=user_message,
            start_time=time.time(),
        )
        self._stack: list[Span] = []  # 当前 span 栈，用于自动挂父子关系

    @contextmanager
    def span(self, name: str, **meta: Any) -> Iterator[Span]:
        """
        创建一个 span 并自动管理父子关系和生命周期。

        用法::

            with trace.span("semantic_search", engine="chromadb") as span:
                results = search(query)
                span.output = {"result_count": len(results)}
                span.metadata["model"] = "all-MiniLM-L6-v2"
        """
        parent_id = self._stack[-1].span_id if self._stack else None
        s = Span(
            span_id=f"sp_{uuid.uuid4().hex[:8]}",
            name=name,
            parent_id=parent_id,
            start_time=time.time(),
            metadata=meta or {},
        )
        self.trace.add_span(s)
        # 如果当前有父 span，也挂到父的 children 里（to_dict 也会做，这里做冗余保证）
        if self._stack:
            self._stack[-1].children.append(s)

        self._stack.append(s)
        try:
            yield s
            if s.status == "running":
                s.finish("success")
        except Exception as exc:
            s.finish("error", str(exc))
            raise
        finally:
            self._stack.pop()

    def finish(self):
        """标记整个 trace 完成。"""
        # 自动结束所有未结束的 span
        for s in self.trace.spans:
            if s.status == "running":
                s.finish("success")
        self.trace.finish()


# ---------------------------------------------------------------------------
#  TraceStore — 全局环形缓冲区
# ---------------------------------------------------------------------------

class TraceStore:
    """
    全局 Trace 存储，环形缓冲区保留最近 N 条。

    用法::

        TraceStore.add(trace)
        recent = TraceStore.get_recent(limit=20)
        one = TraceStore.get(trace_id)
        session_traces = TraceStore.get_by_session(session_id)
    """

    _max: int = 200
    _traces: deque[Trace] = deque()
    _by_id: dict[str, Trace] = {}
    _by_session: dict[str, list[str]] = {}  # session_id → [trace_id, ...]

    @classmethod
    def add(cls, trace: Trace):
        """存入一条 trace，超出上限时淘汰最旧的。"""
        cls._traces.append(trace)
        cls._by_id[trace.trace_id] = trace

        # session 索引
        sid = trace.session_id
        if sid not in cls._by_session:
            cls._by_session[sid] = []
        cls._by_session[sid].append(trace.trace_id)

        # 淘汰
        while len(cls._traces) > cls._max:
            old = cls._traces.popleft()
            cls._by_id.pop(old.trace_id, None)

    @classmethod
    def get(cls, trace_id: str) -> Trace | None:
        return cls._by_id.get(trace_id)

    @classmethod
    def get_recent(cls, limit: int = 20) -> list[Trace]:
        """返回最近 N 条（最新的在前）。"""
        items = list(cls._traces)
        items.reverse()
        return items[:limit]

    @classmethod
    def get_by_session(cls, session_id: str, limit: int = 20) -> list[Trace]:
        """返回某个 session 的最近 N 条 trace。"""
        ids = cls._by_session.get(session_id, [])
        traces = []
        for tid in reversed(ids[-limit:]):
            t = cls._by_id.get(tid)
            if t:
                traces.append(t)
        return traces

    @classmethod
    def stats(cls) -> dict:
        """返回 TraceStore 的简要统计。"""
        return {
            "total_stored": len(cls._traces),
            "max_capacity": cls._max,
            "active_sessions": len(cls._by_session),
        }
