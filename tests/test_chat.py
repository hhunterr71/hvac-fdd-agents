from types import SimpleNamespace as NS

from hvac_fdd_agents.chat.agent import Chat
from hvac_fdd_agents.site.snapshot import Site


def message(blocks, stop):
    usage = NS(input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0)
    return NS(content=blocks, stop_reason=stop, usage=usage)


def text(t):
    return NS(type="text", text=t)


def tool(id_, name, **inp):
    return NS(type="tool_use", id=id_, name=name, input=inp)


class FakeClient:
    """Stands in for anthropic.Anthropic: replays scripted messages, records each request."""

    def __init__(self, messages):
        self.queue = list(messages)
        self.requests = []
        self.beta = NS(messages=NS(stream=self.stream))

    def stream(self, **kw):
        self.requests.append({**kw, "messages": list(kw["messages"])})
        msg = self.queue.pop(0)

        class Stream:
            def __enter__(s):
                return s

            def __exit__(s, *a):
                return False

            def __iter__(s):
                return iter([NS(type="text", text=b.text) for b in msg.content if b.type == "text"])

            def get_final_message(s):
                return msg
        return Stream()


def test_tool_loop_requests_a_work_order_that_waits_for_approval(site_dir):
    client = FakeClient([
        message([text("Checking. "), tool("t1", "request_work_order", unit="VAV-1", fault="damper_stuck",
                                          reason="Damper flat at 0.")], "tool_use"),
        message([text("Requested WO-001; it needs your approval.")], "end_turn"),
    ])
    chat = Chat(Site(site_dir), client=client)
    events = list(chat.send("Please open a work order for VAV-1's damper"))
    assert [e.kind for e in events] == ["text", "tool_call", "tool_result", "text"]
    assert not events[2].data["is_error"] and '"pending_approval"' in events[2].text
    assert Site(site_dir).work_orders("pending_approval")[0]["unit"] == "VAV-1"
    second = client.requests[1]["messages"]
    assert second[-1]["content"][0]["tool_use_id"] == "t1"              # result answers the call
    assert client.requests[0]["tools"][0]["name"] == "site_overview"
    assert chat.usage.calls == 2


def test_tool_errors_go_back_to_the_model(site_dir):
    client = FakeClient([message([tool("t1", "unit_details", unit="VAV-99")], "tool_use"),
                         message([text("There is no VAV-99.")], "end_turn")])
    events = list(Chat(Site(site_dir), client=client).send("What about VAV-99?"))
    result = next(e for e in events if e.kind == "tool_result")
    assert result.data["is_error"] and "unknown unit" in result.text
    assert client.requests[1]["messages"][-1]["content"][0]["is_error"] is True


def test_cut_off_tool_call_is_closed_so_history_stays_valid(site_dir):
    client = FakeClient([message([tool("t1", "site_overview")], "max_tokens")])
    chat = Chat(Site(site_dir), client=client)
    events = list(chat.send("hi"))
    assert events[-1].kind == "notice" and "cut off" in events[-1].text
    last = chat.messages[-1]
    assert last["role"] == "user" and last["content"][0]["tool_use_id"] == "t1"


def test_conversation_stops_at_its_cost_limit(site_dir):
    client = FakeClient([message([text("hi")], "end_turn")])
    chat = Chat(Site(site_dir), client=client, max_cost_usd=0.0)
    events = list(chat.send("hello"))
    assert events[-1].kind == "notice" and "limit" in events[-1].text
    assert client.requests == []                                        # no API call made
