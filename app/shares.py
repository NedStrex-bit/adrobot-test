"""Integer shares: preserve pinned percentages, split remainder equally."""
def rebalance(offers):
    active = [o for o in offers if not o.get('removed')]
    if not active:
        raise ValueError('В потоке должен остаться хотя бы один оффер')
    pinned = [o for o in active if o.get('pinned')]
    total = sum(o['share'] for o in pinned)
    if total > 100:
        raise ValueError('Закреплённые доли превышают 100%')
    free = [o for o in active if not o.get('pinned')]
    if not free and total != 100:
        raise ValueError('Сумма закреплённых долей должна быть 100%')
    if free:
        base, remainder = divmod(100 - total, len(free))
        for i, offer in enumerate(free):
            offer['share'] = base + (i < remainder)
    for offer in offers:
        if offer.get('removed'):
            offer['share'] = 0
    return offers
