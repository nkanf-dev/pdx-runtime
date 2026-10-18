"""Packed Data receipts for current building-IO and cache behavior tests only."""
from dataclasses import replace
from types import SimpleNamespace

from pdx_runtime import v3_data as data
from pdx_runtime.v3_game.economy import building_io as io

BUILDING, GOODS, BUILDING_ID = 913, 927, 0xff00005e


def type_row(name, type_id):
    return data.TypeInfo.from_bytes(data.TYPE_INFO.pack(
        type_id, len(name), 0, 1, 0x100, 0x200, 0x300, name.encode(), b""))


def function_row(receiver, name, arguments):
    return data.FunctionInfo.from_bytes(data.FUNCTION_INFO.pack(
        receiver, 2001, arguments, 2, len(name), 0, 0x100, 0x200, 0, 0, 0, name.encode(), b""))


class Fixture:
    def __init__(self, *, counts=None):
        self.types = [None, type_row("Building", BUILDING), None,
            type_row("Goods", GOODS), type_row("Other", 945)]
        self.functions = {
            BUILDING: [function_row(BUILDING, name, args) for name, args in
                [("AccessProducedGoods", 0), ("AccessConsumedGoods", 0)] + [(name, 1) for name in io.GETTERS]],
            GOODS: [function_row(GOODS, name, 0) for name in ("Self", "GetKey", "GetName")]}
        self.calls, self.completions = [], []
        self.next_handle = 100
        self.counts, self.count_index = counts, 0

    def submit(self, client, initial, *, adapter, process, decode=None):
        fields = data.REQUEST.unpack_from(initial)
        action, type_id, start, capacity = fields[2], fields[4], fields[5], fields[6]
        expression = initial[fields[20]:fields[20] + fields[21]].decode("utf-8")
        bindings = [data.BINDING_INPUT.unpack_from(initial, fields[22] + index * data.BINDING_INPUT.size)
            for index in range(fields[23])]
        tag = expression or f"{action}:{fields[14]}"
        call = {"action": action, "type_id": type_id, "start": start,
            "expression": expression, "bindings": bindings, "handle": fields[14], "tag": tag}
        self.calls.append(call)
        result = replace(data.DataResult.from_bytes(initial), error=0)
        if action in (data.Action.TYPES, data.Action.FUNCTIONS):
            table = self.types if action == data.Action.TYPES else self.functions[type_id]
            next_ = min(start + capacity, len(table))
            rows = tuple(row for row in table[start:next_] if row is not None)
            result = replace(result, rows=rows, count=len(rows), next=next_, total=len(table))
        elif action == data.Action.EVALUATE:
            self.next_handle += 1
            if expression.startswith("Building.Access"):
                result = replace(result, kind=data.Kind.LIST, value_handle=self.next_handle)
            elif expression.startswith("Goods."):
                value = b"paper" if expression.endswith("GetKey") else b"Paper"
                result = replace(result, kind=data.Kind.STRING, value_handle=self.next_handle,
                    snapshot=value, snapshot_bytes=len(value), total=len(value))
            else:
                result = replace(result, kind=data.Kind.FIXED, value_handle=self.next_handle,
                    raw_bits=0xfffffffffffffff1)
        elif action == data.Action.LIST_COUNT:
            total = self.counts[min(self.count_index, len(self.counts)-1)] if self.counts else 2
            self.count_index += 1
            result = replace(result, kind=data.Kind.LIST, total=total)
        elif action == data.Action.LIST_ELEMENT:
            self.next_handle += 1
            result = replace(result, kind=data.Kind.OBJECT, type_id=GOODS, object=0x1234,
                value_handle=self.next_handle, native_type_name=io.OWNED_GOODS_ANY)
        elif action == data.Action.RELEASE:
            result = replace(result, value_handle=0)
        else:
            raise AssertionError(f"unexpected action {action}")
        query = SimpleNamespace(result=result, tag=tag, call=call)
        call["query"] = query
        return query

    async def complete(self, label, query):
        self.completions.append(query)
        return query.result

    def expressions(self):
        return [call["expression"] for call in self.calls if call["expression"]]
