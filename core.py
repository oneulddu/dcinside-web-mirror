import asyncio
import dc_api
import base64
import requests
MAX_PAGE = 31
async def async_read(api_id, board):
    data = {}
    comments = []
    images = []
    async with dc_api.API() as api:
        doc = await api.document(board_id=board, document_id=api_id)
        data = {
            "title": doc.title,
            "author": doc.author,
            "time": doc.time,
            "voteup_count": doc.voteup_count,
            "html": doc.html,
            
        }
        async for com in doc.comments():
            dccon = None
            if com.dccon != None:
                dccon = "data:image/gif;base64," + base64.b64encode(requests.get(com.dccon).content).decode('utf-8')
            t = {
                "time": com.time,
                "contents": com.contents,
                "author": com.author,
                "dccon": dccon,
            }
            comments.append(t)
        for img in doc.images:
            src = await img.load()
            string = base64.b64encode(src).decode('utf-8')
            images.append(string)
    return data, comments, images


async def async_index(page, board, recommend):
    data = []
    async with dc_api.API() as api:
        async for item in api.board(board_id=board,
                                    num=MAX_PAGE, start_page=page, recommend=recommend):
            tdata = {
                "id": item.id,
                "title": item.title,
                "author": item.author,
                "time": item.time,
                "comment_count": item.comment_count,
                "voteup_count": item.voteup_count,
                "view_count": item.view_count,
                "isimage": item.isimage,
                "isrecommend": item.isrecommend,
                "isdcbest": item.isdcbest,
                "ishit": item.ishit
            }
            data.append(tdata)
    return data