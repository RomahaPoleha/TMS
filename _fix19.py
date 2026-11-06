content = open('inventory/views.py', encoding='utf-8').read()

old = """    return render(request, "inventory/movement_log.html", {
        "page_obj": page_obj,
        "query": query,
    })
    query = request.GET.get('q', '')
    logs = MovementLog.objects.select_related('client', 'reservation').order_by('-created_at')

    if query:
        logs = logs.filter(
            Q(serial_number__icontains=query) |
            Q(comment__icontains=query)
        )

    from django.core.paginator import Paginator
    paginator = Paginator(logs, 50)
    page = request.GET.get('page', 1)
    page_obj = paginator.get_page(page)

    return render(request, "inventory/movement_log.html", {
        "page_obj": page_obj,
        "query": query,
    })"""

new = """    return render(request, "inventory/movement_log.html", {
        "page_obj": page_obj,
        "query": query,
    })"""

assert old in content, 'old not found'
content = content.replace(old, new)
open('inventory/views.py', 'w', encoding='utf-8').write(content)
print('Done')