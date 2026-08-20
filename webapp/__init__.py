"""tw-stock-scanner Web 套件 — import 即完成全部路由註冊"""
from webapp.core import app
import webapp.views.selection  # noqa: F401,E402
import webapp.views.stock  # noqa: F401,E402
import webapp.views.chips  # noqa: F401,E402
import webapp.views.market_report  # noqa: F401,E402
import webapp.views.market_state  # noqa: F401,E402
import webapp.views.derivatives  # noqa: F401,E402
import webapp.views.heatmaps  # noqa: F401,E402
import webapp.views.research  # noqa: F401,E402
import webapp.views.broker_reports  # noqa: F401,E402
import webapp.views.system  # noqa: F401,E402
__all__ = ['app']
