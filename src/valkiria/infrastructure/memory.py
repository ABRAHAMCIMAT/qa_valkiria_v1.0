from collections import defaultdict
class InMemoryAudit:
    def __init__(self): self.events=[]
    async def append(self,event): self.events.append(event)
class InMemoryMetrics:
    def __init__(self): self.items=[]
    async def record(self,item): self.items.append(item)
class InMemoryStories:
    def __init__(self): self.items=defaultdict(dict)
    async def save(self,story): self.items[str(story.id)]=story; return story
    async def get(self,story_id): return self.items.get(story_id)
