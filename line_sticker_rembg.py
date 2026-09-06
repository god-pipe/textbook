# -*- coding: utf-8 -*-
"""
line_sticker_rembg.py
========================================================================
rembg 可選整合模組 —— 通用物件去背，適合小動物、Q版角色、飲料/小物道具
這類「不是真人」的貼圖素材。

為什麼推薦這個當 RVM 的替代/補充：
    RVM (RobustVideoMatting) 是用真人半身/全身影片訓練的，專長是頭髮絲、
    真人膚色邊緣這類細節，拿去處理插畫風格的小動物、Q版角色、商品小物，
    效果常常不如預期（畢竟訓練資料裡沒看過這些）。
    rembg 底層用的是 U^2-Net / IS-Net 這類「通用顯著物件分割」模型，
    訓練資料涵蓋各種物件類別（不只人），對插畫、卡通、商品去背這種
    「主體 vs 背景」界線清楚的素材效果通常更好、更穩定。

    另外一個實務上的優點：rembg 走 onnxruntime，不需要裝 torch/torchvision
    這麼重的框架，純 CPU 也能跑得動（模型檔通常幾MB~幾十MB，比 RVM 的
    ResNet50版或動輒上百MB的模型都輕量很多），符合「不吃重效能」的需求。

安裝方式（使用者自行在自己的環境執行）：
    pip install rembg
    第一次執行時，rembg 會自動從網路下載對應的模型檔（存到 ~/.u2net/），
    只有第一次需要網路，之後會用本機快取。

可用模型（用 model_name 參數指定）：
    - "isnet-general-use"：目前品質最好的通用模型，預設用這個
    - "u2net"：經典款，泛用性也不錯
    - "u2netp"：u2net 的輕量版（~4.5MB），最省資源、速度最快，
      犧牲一些邊緣精細度，適合大量批次處理或機器效能有限時用
    - "silueta"：另一個輕量選項

本模組跟 line_sticker_rvm.py 一樣，把 rembg 的 import 包在函式裡面，
沒裝的話 is_rembg_available() 會回傳 False，不會讓整個工具因此掛掉。
========================================================================
"""

from PIL import Image


def is_rembg_available() -> bool:
    try:
        import rembg  # noqa: F401
        return True
    except ImportError:
        return False


class RembgMatter:
    """
    包裝 rembg，逐張影格去背（不像 RVM 有時序記憶，是每張獨立處理）。
    對變化不大的物件（商品、道具、Q版角色）通常已經夠穩定；如果是動作
    幅度很大的角色，逐幀獨立處理仍可能有邊緣輕微跳動，這是 rembg 的
    已知限制，不像 RVM 那樣有跨幀一致性保證。
    """

    def __init__(self, model_name: str = "isnet-general-use"):
        if not is_rembg_available():
            raise RuntimeError(
                "找不到 rembg，請先執行：pip install rembg\n"
                "（不需要額外裝 torch，走的是 onnxruntime，CPU 也能跑）"
            )
        from rembg import new_session
        try:
            self.session = new_session(model_name)
        except Exception as e:
            raise RuntimeError(
                f"rembg 模型（{model_name}）載入/下載失敗，第一次使用需要網路連線"
                "以下載模型檔（之後會快取在本機 ~/.u2net/，離線也能用）。\n"
                f"原始錯誤：{e}"
            ) from e

    def matte_single(self, pil_rgb_img: Image.Image) -> Image.Image:
        from rembg import remove
        result = remove(pil_rgb_img.convert("RGB"), session=self.session)
        return result.convert("RGBA")

    def matte_sequence(self, pil_rgb_frames):
        return [self.matte_single(img) for img in pil_rgb_frames]
