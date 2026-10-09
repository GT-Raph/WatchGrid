from rest_framework.pagination import PageNumberPagination


class StandardPagination(PageNumberPagination):
    """``{count, next, previous, results}`` envelope (Section 20.4)."""

    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100
