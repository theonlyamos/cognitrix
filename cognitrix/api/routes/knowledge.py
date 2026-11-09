"""Knowledge base: the signed-in person's document collections. Browser
sessions only; agents reach the documents through the Search Knowledge tool.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from cognitrix import knowledge
from cognitrix.agents import access as agent_access
from cognitrix.common.security import AuthContext, jwt_only
from cognitrix.knowledge.models import KnowledgeDocument

knowledge_api = APIRouter(prefix='/knowledge')


class CollectionBody(BaseModel):
    name: str
    description: str = ''


class SearchBody(BaseModel):
    query: str


def _owner(ctx: AuthContext) -> str:
    return agent_access.owner_key(ctx.user)


async def _collection(collection_id: str, ctx: AuthContext):
    row = await knowledge.get_collection(_owner(ctx), collection_id)
    if row is None:
        raise HTTPException(status_code=404, detail='Collection not found')
    return row


async def _project(row) -> dict:
    docs = await knowledge.documents(str(row.id))
    return {
        'id': str(row.id), 'name': row.name, 'description': row.description, 'created': row.created,
        'documents': [knowledge.project_document(doc) for doc in docs],
    }


@knowledge_api.get('')
async def list_collections(ctx: AuthContext = Depends(jwt_only)):
    return [await _project(row) for row in await knowledge.collections(_owner(ctx))]


@knowledge_api.post('')
async def create_collection(body: CollectionBody, ctx: AuthContext = Depends(jwt_only)):
    try:
        row = await knowledge.create_collection(_owner(ctx), body.name, body.description)
    except knowledge.KnowledgeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await _project(row)


@knowledge_api.get('/{collection_id}')
async def get_collection(collection_id: str, ctx: AuthContext = Depends(jwt_only)):
    return await _project(await _collection(collection_id, ctx))


@knowledge_api.delete('/{collection_id}')
async def delete_collection(collection_id: str, ctx: AuthContext = Depends(jwt_only)):
    await knowledge.delete_collection(await _collection(collection_id, ctx))
    return {'message': 'Collection deleted'}


@knowledge_api.post('/{collection_id}/documents')
async def add_document(collection_id: str, file: UploadFile = File(...), ctx: AuthContext = Depends(jwt_only)):
    row = await _collection(collection_id, ctx)
    data = await file.read(knowledge.MAX_DOCUMENT_BYTES + 1)
    try:
        document = await knowledge.add_document(row, file.filename or 'document', data)
    except knowledge.KnowledgeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return knowledge.project_document(document)


@knowledge_api.delete('/{collection_id}/documents/{document_id}')
async def delete_document(collection_id: str, document_id: str, ctx: AuthContext = Depends(jwt_only)):
    row = await _collection(collection_id, ctx)
    document = await KnowledgeDocument.get(document_id)
    if document is None or str(document.collection_id) != str(row.id):
        raise HTTPException(status_code=404, detail='Document not found')
    await knowledge.delete_document(document)
    return {'message': 'Document deleted'}


@knowledge_api.post('/{collection_id}/search')
async def search_collection(collection_id: str, body: SearchBody, ctx: AuthContext = Depends(jwt_only)):
    row = await _collection(collection_id, ctx)
    return await knowledge.search(_owner(ctx), [str(row.id)], body.query, 6)
