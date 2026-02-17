import dc_api
# 프로그래밍 갤러리 글 무한 크롤링
import asyncio
import dc_api
import json
_json = {}

async def run():
    global _json
    async with dc_api.API() as api:
        # _json = await api.gallery_miner()
        # print(a)
        async for i in api.board(board_id="lightemittingdiode", num=10):
            print(i.title)
        

asyncio.run(run())
# with open("gallerys_miner_game.json", "w", encoding="utf-16") as f:
#     json.dump(_json, f, indent=4, ensure_ascii=False)