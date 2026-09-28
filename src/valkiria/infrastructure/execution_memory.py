from __future__ import annotations

class InMemoryExecutionStore:
    def __init__(self):
        self.items = {}
    async def save(self, item):
        self.items[str(item["id"])] = item
        return item
    async def get(self, execution_id):
        return self.items.get(str(execution_id))

class InMemoryReportStore:
    def __init__(self):
        self.items = {}
    async def save(self, item):
        self.items[str(item["id"])] = item
        return item
    async def get(self, report_id):
        return self.items.get(str(report_id))

class InMemoryBatchStore:
    def __init__(self):
        self.items = {}
    async def save(self, item):
        self.items[str(item["id"])] = item
        return item
    async def get(self, batch_id):
        return self.items.get(str(batch_id))
