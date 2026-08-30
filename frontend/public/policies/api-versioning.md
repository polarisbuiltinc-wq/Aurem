# API Versioning & Deprecation Policy

**Last updated: August 30, 2026**

## Versioning

The AUREM API is versioned at the endpoint path level (e.g. `/api/aurem-dev/...`). The interactive spec is always current at:

- Swagger UI: https://auremcto.com/api/docs
- ReDoc: https://auremcto.com/api/redoc
- Raw OpenAPI JSON: https://auremcto.com/api/openapi.json

## Deprecation policy

When an endpoint or field is scheduled for removal:

1. It is marked deprecated in the OpenAPI spec (visible at `/api/docs`) at least 30 days before removal.
2. Responses from a deprecated endpoint carry a `Deprecation` header (RFC 8594) with the deprecation date, and a `Sunset` header with the planned removal date once one is set.
3. We do not remove an endpoint that active paying customers still call without direct notice first.

## Breaking vs. non-breaking changes

Adding new optional fields, new endpoints, or new headers is never treated as breaking. Removing a field, changing a field's type, or removing an endpoint is breaking and follows the deprecation policy above.

## Questions

Contact **security@auremcto.com** for API deprecation questions, or open a ticket at auremcto.com/support.
