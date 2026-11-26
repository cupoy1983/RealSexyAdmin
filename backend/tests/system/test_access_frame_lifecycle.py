from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from app.plugin.infra.crawler.access_signals import inspect_access

class TargetClosedError(Exception):
    pass

class Frame:
    def __init__(self, *, visible=False, broken=False, detached=False):
        self.detached = detached
        self.group = SimpleNamespace()
        self.group.first = self.group
        self.group.is_visible = AsyncMock(return_value=visible, side_effect=TargetClosedError('Target page, context or browser has been closed') if broken else None)
        self.group.all = AsyncMock(return_value=[])
    def locator(self, _):return self.group
    def is_detached(self):return self.detached
    def get_by_text(self,*args,**kwargs):return SimpleNamespace(first=SimpleNamespace(is_visible=AsyncMock(return_value=False)))

class Page:
    url='https://fixture.invalid/'
    def __init__(self, snapshots, main):self.snapshots,self.main_frame,self.reads=snapshots,main,0
    @property
    def frames(self):
        value=self.snapshots[min(self.reads,len(self.snapshots)-1)];self.reads+=1;return value
    def is_closed(self):return False
    def locator(self,_):return SimpleNamespace(inner_text=AsyncMock(return_value='Fixture homepage'))

SITE={'domains':['fixture.invalid'],'challenge_selectors':['#challenge']}

async def test_detached_child_does_not_fail_live_main_page():
    main,old=Frame(),Frame(broken=True,detached=True)
    page=Page([[main,old],[main]],main)
    result=await inspect_access(page,site=SITE)
    assert result['state']=='unknown' and result['reason']=='unrecognized_page'
    assert page.reads==2

async def test_replacement_challenge_frame_is_still_detected():
    main,old,replacement=Frame(),Frame(broken=True,detached=True),Frame(visible=True)
    result=await inspect_access(Page([[main,old],[main,replacement]],main),site=SITE)
    assert result['state']=='verification_required'

async def test_main_frame_error_is_not_suppressed():
    main=Frame(broken=True,detached=True)
    with pytest.raises(TargetClosedError):await inspect_access(Page([[main]],main),site=SITE)

async def test_attached_child_error_is_not_suppressed():
    main,child=Frame(),Frame(broken=True,detached=False)
    with pytest.raises(TargetClosedError):await inspect_access(Page([[main,child]],main),site=SITE)

async def test_continuously_changing_frames_do_not_become_valid():
    main,old=Frame(),Frame(broken=True,detached=True)
    result=await inspect_access(Page([[main,old]],main),site=SITE)
    assert result['state']=='unknown' and result['reason']=='page_changing'
