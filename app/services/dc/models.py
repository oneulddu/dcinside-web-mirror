import filetype


MOBILE_USER_AGENT = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
GET_HEADERS = {
    "User-Agent": MOBILE_USER_AGENT,
}
GALLERY_POSTS_COOKIES = {
    "__gat_mobile_search": 1,
    "list_count": 200,
}


class DocumentIndex:
    __slots__ = ["id", "subject", "title", "board_id", "has_image", "has_video", "author", "author_id", "time", "view_count", "comment_count", "voteup_count",
            "document", "comments", "isimage", "isvideo", "isrecommend", "isdcbest", "ishit", "is_mobile_source"]
    def __init__(self, id, board_id, title, has_image, author, author_id, time, view_count, comment_count, voteup_count, document, comments, subject, isimage, isrecommend, isdcbest, ishit, is_mobile_source=False, has_video=False, isvideo=False):
        self.id = id
        self.board_id = board_id
        self.title = title
        self.has_image = has_image
        self.has_video = has_video
        self.author = author
        self.author_id = author_id
        self.time = time
        self.view_count = view_count
        self.comment_count = comment_count
        self.voteup_count = voteup_count
        self.document = document
        self.comments = comments
        self.subject = subject
        self.isimage = isimage
        self.isvideo = isvideo
        self.isrecommend = isrecommend
        self.isdcbest = isdcbest
        self.ishit = ishit
        self.is_mobile_source = bool(is_mobile_source)
    def __str__(self):
        return f"{self.subject or ''}\t|{self.id}\t|{self.time.isoformat()}\t|{self.author}\t|{self.title}({self.comment_count}) +{self.voteup_count}"

class Document:
    __slots__ = ["id", "board_id", "title", "author", "author_id", "contents", "images", "html", "view_count", "voteup_count", "votedown_count", "logined_voteup_count", "time", "subject", "comments", "is_mobile_source", "related_posts", "embedded_comments", "embedded_comment_total"]
    def __init__(self, id, board_id, title, author, author_id, contents, images, html, view_count, voteup_count, votedown_count, logined_voteup_count, time, comments, subject=None, is_mobile_source=False, related_posts=None, embedded_comments=None, embedded_comment_total=0):
        self.id = id
        self.board_id = board_id
        self.title = title
        self.author = author
        self.author_id = author_id
        self.contents = contents
        self.images = images
        self.html = html
        self.view_count = view_count
        self.voteup_count = voteup_count
        self.votedown_count = votedown_count
        self.logined_voteup_count = logined_voteup_count
        self.comments = comments
        self.time = time
        self.subject = subject
        self.is_mobile_source = bool(is_mobile_source)
        self.related_posts = list(related_posts or [])
        self.embedded_comments = list(embedded_comments or [])
        self.embedded_comment_total = embedded_comment_total
    def __str__(self):
        return f"{self.subject or ''}\t|{self.id}\t|{self.time.isoformat()}\t|{self.author}\t|{self.title}({self.comment_count}) +{self.voteup_count} -{self.votedown_count}\n{self.contents}"

class Comment:
    __slots__ = ["id", "parent_id", "author", "author_id", "contents", "dccon", "voice", "time", "is_reply"]
    def __init__(self, id, parent_id, author, author_id, contents, dccon, voice, time, is_reply=False):
        self.id = id
        self.parent_id = parent_id
        self.author = author
        self.author_id = author_id
        self.contents = contents
        self.dccon = dccon
        self.voice = voice
        self.time = time
        self.is_reply = bool(is_reply)
    def __str__(self):
        return f"ㄴ {self.author}: {self.contents or ''}{self.dccon or ''}{self.voice or ''} | {self.time}"

class Image:
    __slots__ = ["src", "document_id", "board_id", "session"]
    def __init__(self, src, document_id, board_id, session):
        self.src = src
        self.document_id = document_id
        self.board_id = board_id
        self.session = session
    async def load(self):
        headers = GET_HEADERS.copy()
        headers["Referer"] = "https://m.dcinside.com/board/{}/{}".format(self.board_id, self.document_id)
        async with self.session.get(self.src, cookies=GALLERY_POSTS_COOKIES, headers=headers) as res:
            return await res.read()
    async def download(self, path):
        headers = GET_HEADERS.copy()
        headers["Referer"] = "https://m.dcinside.com/board/{}/{}".format(self.board_id, self.document_id)
        async with self.session.get(self.src, cookies=GALLERY_POSTS_COOKIES, headers=headers) as res:
            bytes = await res.read()
            ext = filetype.guess(bytes).extension
            with open(path + '.' + ext, 'wb') as f:
                f.write(bytes)



