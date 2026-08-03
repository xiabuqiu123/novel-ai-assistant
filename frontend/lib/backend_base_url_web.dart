String getDefaultBackendBaseUrl() {
  const configured = String.fromEnvironment('BACKEND_BASE_URL');
  return configured.isNotEmpty ? configured : 'http://127.0.0.1:8000';
}
