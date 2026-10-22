from rest_framework.views import exception_handler as drf_handler
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from django.core.exceptions import TooManyFilesSent, RequestDataTooBig

class Conflict(APIException):
    status_code = 409
    default_detail = '该工单已被更新，请刷新后再提交。输入内容仍会保留。'

def exception_handler(exc, context):
    if isinstance(exc, TooManyFilesSent):
        return Response({'detail': '每次最多上传 5 个附件。'}, status=400)
    if isinstance(exc, RequestDataTooBig):
        return Response({'detail': '提交的正文过大。'}, status=413)
    response = drf_handler(exc, context)
    if response is not None and 'detail' not in response.data:
        response.data = {'detail': '请检查输入内容。', 'fields': response.data}
    return response
