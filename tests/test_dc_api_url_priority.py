from app.services.dc_api import API


def test_list_urls_prefer_pc_before_mobile():
    api = API.__new__(API)
    urls = api._API__build_list_urls("aoegame", 1, recommend=False, kind=None)
    assert urls[0].startswith("https://gall.dcinside.com/")
    assert urls[-1].startswith("https://m.dcinside.com/")


def test_list_urls_keep_recommend_flag_on_pc():
    api = API.__new__(API)
    urls = api._API__build_list_urls("aoegame", 1, recommend=True, kind="minor")
    assert "recommend=1" in urls[0]
    assert urls[0].startswith("https://gall.dcinside.com/mgallery/")


def test_view_urls_prefer_pc_before_mobile():
    api = API.__new__(API)
    urls = api._API__build_view_urls("aoegame", "30389383", kind="minor")
    assert urls[0].startswith("https://gall.dcinside.com/")
    assert urls[-1].startswith("https://m.dcinside.com/")
