from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

spectacular_patterns = [
    path('swagger-ui/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('', SpectacularAPIView.as_view(), name='schema'),
]

api_patterns = [
    path('schema/', include(spectacular_patterns)),
    path('accounts/', include('accounts.urls')),
    path('chat/', include('chat.urls')),
]

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include(api_patterns)),
    path('', RedirectView.as_view(url='api/schema/swagger-ui/')),
]
