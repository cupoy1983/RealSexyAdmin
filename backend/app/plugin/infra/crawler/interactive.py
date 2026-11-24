"""当前页面的人工交互原语；调用方负责权限、业务与允许的网站。"""

import base64
from uuid import uuid4


async def frame(page, mask_selector="input[type=password],input[type=email]"):
    viewport = await page.evaluate("({width:innerWidth,height:innerHeight})")
    image = await page.screenshot(type="jpeg", quality=55, full_page=False, scale="css", timeout=5000,
                                  mask=[page.locator(mask_selector)])
    if len(image) > 512000:
        raise ValueError("人工接管截图超过大小限制")
    return {"frame_id": uuid4().hex, "image": base64.b64encode(image).decode(), **viewport}


async def interact(page, command):
    action = command["action"]
    if action == "click":
        await page.mouse.click(command["x"], command["y"])
    elif action == "text":
        if await page.evaluate("!!document.activeElement?.matches('input[type=password],input[type=email]')"):
            raise ValueError("人工接管不作为账号登录入口")
        await page.keyboard.insert_text(command.get("secret") or "")
    elif action in {"enter", "backspace", "tab"}:
        await page.keyboard.press({"enter": "Enter", "backspace": "Backspace", "tab": "Tab"}[action])
