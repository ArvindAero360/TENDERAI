from django.urls import path
from .views import (
    IndexView,
    GetTokenView,
    PostTokenView,
    TestConnectionView,
    ExtractView,
    RunExtractionView,
    CreateBidView,
    ProxyPostView,
)

urlpatterns = [
    # UI
    path('',               IndexView.as_view(),          name='index'),
    # Token endpoints
    path('get-token',      GetTokenView.as_view(),        name='get_token'),
    path('post-token',     PostTokenView.as_view(),       name='post_token'),
    # Diagnostics
    path('test',           TestConnectionView.as_view(),  name='test_connection'),
    # Core extraction
    path('extract',        ExtractView.as_view(),         name='extract'),
    # Submission / proxy
    path('run_extraction', RunExtractionView.as_view(),   name='run_extraction'),
    path('create_bid',     CreateBidView.as_view(),       name='create_bid'),
    path('proxy_post',     ProxyPostView.as_view(),       name='proxy_post'),
]
