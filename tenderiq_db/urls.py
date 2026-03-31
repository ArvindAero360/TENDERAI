from django.urls import path, include

urlpatterns = [
    # ── Development ──
    # path('', include('extractor.urls')),
    
    # ── Production ──
    path('api4/', include('extractor.urls')),
]