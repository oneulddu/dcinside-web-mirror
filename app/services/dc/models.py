class DocumentIndex:
    __slots__ = ["id", "subject", "title", "board_id", "has_image", "has_video", "author", "author_id", "author_role", "time", "time_text", "time_is_precise", "view_count", "comment_count", "voteup_count",
            "document", "comments", "isimage", "isvideo", "isrecommend", "isdcbest", "ishit", "is_mobile_source"]
    def __init__(self, id, board_id, title, has_image, author, author_id, time, view_count, comment_count, voteup_count, document, comments, subject, isimage, isrecommend, isdcbest, ishit, is_mobile_source=False, has_video=False, isvideo=False, time_text=None, time_is_precise=None, author_role=None):
        self.id = id
        self.board_id = board_id
        self.title = title
        self.has_image = has_image
        self.has_video = has_video
        self.author = author
        self.author_id = author_id
        self.author_role = author_role
        self.time = time
        self.time_text = time_text
        self.time_is_precise = (":" in str(time_text if time_text is not None else time)) if time_is_precise is None else bool(time_is_precise)
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
    __slots__ = ["id", "board_id", "title", "author", "author_id", "author_role", "contents", "images", "html", "view_count", "voteup_count", "votedown_count", "logined_voteup_count", "time", "subject", "comments", "is_mobile_source", "related_posts", "embedded_comments", "embedded_comment_total"]
    def __init__(self, id, board_id, title, author, author_id, contents, images, html, view_count, voteup_count, votedown_count, logined_voteup_count, time, comments, subject=None, is_mobile_source=False, related_posts=None, embedded_comments=None, embedded_comment_total=0, author_role=None):
        self.id = id
        self.board_id = board_id
        self.title = title
        self.author = author
        self.author_id = author_id
        self.author_role = author_role
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
        return f"{self.subject or ''}\t|{self.id}\t|{self.time.isoformat()}\t|{self.author}\t|{self.title} +{self.voteup_count} -{self.votedown_count}\n{self.contents}"

class Comment:
    __slots__ = ["id", "parent_id", "author", "author_id", "author_role", "contents", "dccon", "voice", "time", "is_reply"]
    def __init__(self, id, parent_id, author, author_id, contents, dccon, voice, time, is_reply=False, author_role=None):
        self.id = id
        self.parent_id = parent_id
        self.author = author
        self.author_id = author_id
        self.author_role = author_role
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
