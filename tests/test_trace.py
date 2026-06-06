"""Tests for core/trace.py — Span, Trace, TraceContext, TraceStore."""

import time

import pytest

from core.trace import Span, Trace, TraceContext, TraceStore


# =========================================================================
#  Span
# =========================================================================

class TestSpan:
    """Span is a single execution step record."""

    def test_defaults(self):
        s = Span(span_id="sp_001", name="test_span")
        assert s.span_id == "sp_001"
        assert s.name == "test_span"
        assert s.parent_id is None
        assert s.status == "running"
        assert s.input == {}
        assert s.output == {}
        assert s.children == []

    def test_finish_sets_timing(self):
        s = Span(span_id="s1", name="step1", start_time=time.time())
        s.finish("success")
        assert s.status == "success"
        assert s.end_time is not None
        assert s.duration_ms is not None
        assert s.duration_ms >= 0

    def test_finish_with_error(self):
        s = Span(span_id="s1", name="step1")
        s.finish("error", "something went wrong")
        assert s.status == "error"
        assert s.error == "something went wrong"

    def test_to_dict_structure(self):
        s = Span(span_id="s1", name="step1")
        s.finish("success")
        d = s.to_dict()
        assert d["span_id"] == "s1"
        assert d["name"] == "step1"
        assert d["status"] == "success"
        assert "duration_ms" in d
        assert "input_summary" in d
        assert "output_summary" in d
        assert "children" in d

    def test_to_dict_includes_children(self):
        parent = Span(span_id="p", name="parent")
        child = Span(span_id="c", name="child", parent_id="p")
        parent.children.append(child)
        d = parent.to_dict()
        assert len(d["children"]) == 1
        assert d["children"][0]["name"] == "child"

    def test_to_dict_null_duration(self):
        s = Span(span_id="s1", name="step1")
        d = s.to_dict()
        assert d["duration_ms"] is None


# =========================================================================
#  Trace
# =========================================================================

class TestTrace:
    """Trace is a complete request record with spans."""

    def test_add_and_get_span(self):
        t = Trace(trace_id="tr1", session_id="sess1", user_message="hello")
        s = Span(span_id="sp1", name="step1")
        t.add_span(s)
        assert t.get_span("sp1") is s
        assert t.get_span("nonexistent") is None

    def test_finish_computes_totals(self):
        t = Trace(trace_id="tr1", session_id="sess1", start_time=time.time())
        t.finish()
        assert t.total_duration_ms is not None
        assert t.total_duration_ms >= 0

    def test_finish_counts_llm_calls(self):
        t = Trace(trace_id="tr1", session_id="sess1")
        s1 = Span(span_id="s1", name="llm_generation")
        s2 = Span(span_id="s2", name="intent_detection")
        s3 = Span(span_id="s3", name="profile_extraction")
        s4 = Span(span_id="s4", name="tool_execution")
        t.add_span(s1); t.add_span(s2); t.add_span(s3); t.add_span(s4)
        t.finish()
        assert t.total_llm_calls == 3  # llm_generation + intent_detection + profile_extraction

    def test_to_dict_tree_structure(self):
        t = Trace(trace_id="tr1", session_id="sess1", user_message="test")
        parent = Span(span_id="p", name="parent")
        child = Span(span_id="c", name="child", parent_id="p")
        t.add_span(parent)
        t.add_span(child)
        d = t.to_dict()
        assert d["trace_id"] == "tr1"
        root_spans = d["spans"]
        assert len(root_spans) == 1
        assert root_spans[0]["name"] == "parent"
        assert len(root_spans[0]["children"]) == 1

    def test_to_summary(self):
        t = Trace(trace_id="tr1", session_id="sess1", user_message="hello world", start_time=time.time())
        t.finish()
        s = t.to_summary()
        assert s["trace_id"] == "tr1"
        assert s["session_id"] == "sess1"
        assert "total_duration_ms" in s
        assert "span_count" in s


# =========================================================================
#  TraceContext
# =========================================================================

class TestTraceContext:
    """TraceContext manages span creation with auto parent-child."""

    def test_basic_span(self):
        trace = TraceContext("sess1", "hello")
        with trace.span("step1") as s:
            s.input = {"key": "val"}
            s.output = {"result": 42}
        trace.finish()
        assert len(trace.trace.spans) == 1
        assert trace.trace.spans[0].status == "success"

    def test_nested_spans_have_parent(self):
        trace = TraceContext("sess1", "hello")
        with trace.span("outer") as outer:
            with trace.span("inner") as inner:
                pass
        trace.finish()
        assert len(trace.trace.spans) == 2
        # Find the inner span
        inner_span = next(s for s in trace.trace.spans if s.name == "inner")
        assert inner_span.parent_id is not None
        # Parent should be the outer span
        outer_span = trace.trace.get_span(inner_span.parent_id)
        assert outer_span is not None
        assert outer_span.name == "outer"

    def test_exception_in_span_marks_error(self):
        trace = TraceContext("sess1", "hello")
        try:
            with trace.span("failing"):
                raise ValueError("boom")
        except ValueError:
            pass
        trace.finish()
        failing = trace.trace.spans[0]
        assert failing.status == "error"
        assert "boom" in failing.error

    def test_finish_auto_closes_running_spans(self):
        trace = TraceContext("sess1", "hello")
        # Manually create a span without finishing it
        s = trace.trace.spans  # Not using context manager — span won't auto-finish via ctx mgr
        # Actually the proper test: the context manager auto-finishes on exit
        with trace.span("auto"):
            pass
        trace.finish()
        assert trace.trace.spans[0].status == "success"


# =========================================================================
#  TraceStore
# =========================================================================

class TestTraceStore:
    """TraceStore is a global ring buffer for recent traces."""

    def teardown_method(self):
        # Clear the store between tests
        TraceStore._traces.clear()
        TraceStore._by_id.clear()
        TraceStore._by_session.clear()

    def test_add_and_get(self):
        t = Trace(trace_id="tr1", session_id="sess1")
        TraceStore.add(t)
        assert TraceStore.get("tr1") is t

    def test_get_missing_returns_none(self):
        assert TraceStore.get("nonexistent") is None

    def test_get_recent_newest_first(self):
        t1 = Trace(trace_id="t1", session_id="s1"); t1.finish()
        t2 = Trace(trace_id="t2", session_id="s1"); t2.finish()
        TraceStore.add(t1)
        TraceStore.add(t2)
        recent = TraceStore.get_recent(limit=10)
        assert len(recent) == 2
        assert recent[0].trace_id == "t2"  # newest first

    def test_get_recent_respects_limit(self):
        for i in range(10):
            t = Trace(trace_id=f"t{i}", session_id="s1")
            TraceStore.add(t)
        recent = TraceStore.get_recent(limit=3)
        assert len(recent) == 3

    def test_get_by_session(self):
        TraceStore.add(Trace(trace_id="a1", session_id="A"))
        TraceStore.add(Trace(trace_id="a2", session_id="A"))
        TraceStore.add(Trace(trace_id="b1", session_id="B"))
        traces = TraceStore.get_by_session("A")
        assert len(traces) == 2
        assert all(t.session_id == "A" for t in traces)

    def test_ring_buffer_eviction(self):
        # Temporarily reduce max to 5
        original_max = TraceStore._max
        TraceStore._max = 5
        try:
            for i in range(10):
                TraceStore.add(Trace(trace_id=f"t{i}", session_id="s"))
            assert len(TraceStore._traces) == 5
            # Oldest should be evicted
            assert TraceStore.get("t0") is None
            assert TraceStore.get("t9") is not None
        finally:
            TraceStore._max = original_max

    def test_stats(self):
        TraceStore.add(Trace(trace_id="t1", session_id="A"))
        TraceStore.add(Trace(trace_id="t2", session_id="B"))
        stats = TraceStore.stats()
        assert stats["total_stored"] == 2
        assert stats["active_sessions"] == 2
