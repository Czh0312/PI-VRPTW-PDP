"""Availability of goods at the fixed batch loading time, without solver imports."""


def fixed_order_availability_errors(problem):
    errors = []
    for order in problem.orders:
        batch = problem.batch_map[order.batch]
        if order.release_time is not None and batch.start < order.release_time:
            errors.append(dict(code='ORDER_RELEASE_AFTER_BATCH_START', order_id=order.id,
                               batch_id=batch.id, batch_start=batch.start, release_time=order.release_time))
        if order.allowed_batch_ids is not None and order.batch not in order.allowed_batch_ids:
            errors.append(dict(code='ORDER_BATCH_NOT_ALLOWED', order_id=order.id, batch_id=batch.id))
    return errors
