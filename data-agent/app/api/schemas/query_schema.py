from pydantic import BaseModel


class QuerySchema(BaseModel):
    query: str
