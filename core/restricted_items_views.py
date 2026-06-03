"""
Views for the restricted items / sale approval system.
"""
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from core.models import Item, ItemSaleApproval, Transaction, Notification
from core.views import get_user_profile, owner_required


# ── Shared helper ─────────────────────────────────────────────────────────────

def _create_approval_request(request, item, user_profile, quantity, recipient, invoice_no, payment_method):
    """Creates approval record and sends owner notifications. Returns the approval."""
    approval = ItemSaleApproval.objects.create(
        business=user_profile.business,
        item=item,
        requested_by=request.user,
        quantity=quantity,
        recipient=recipient,
        invoice_no=invoice_no,
        payment_method=payment_method,
    )

    staff_name = request.user.get_full_name() or request.user.username
    owner_profiles = user_profile.business.users.filter(role='owner')
    for op in owner_profiles:
        Notification.objects.create(
            user=op.user,
            title=f'⚠️ Approval needed — {item.description}',
            message=(
                f'{staff_name} is requesting to sell {quantity} {item.unit} '
                f'of {item.description}'
                f'{" to " + recipient if recipient else ""}. '
                f'Restriction: {item.restriction_notes or "No reason given"}. '
                f'Go to Pending Approvals to approve or deny.'
            ),
            notification_type='warning',
        )

    try:
        from core.notifications import send_sms_notification, normalize_ke_phone
        if user_profile.business.phone:
            phone = normalize_ke_phone(user_profile.business.phone)
            if phone:
                msg = (
                    f'APPROVAL NEEDED: {staff_name} wants to sell '
                    f'{item.description} x{quantity}. '
                    f'Log in to Duka Mwecheche to approve or deny.'
                )
                send_sms_notification(phone, msg, user_profile.business)
    except Exception:
        pass

    return approval


# ── Views ─────────────────────────────────────────────────────────────────────

@login_required
@require_POST
def request_sale_approval(request, item_id):
    """
    Called when staff tries to sell a restricted item via the standalone URL.
    Validates inputs, creates a pending ItemSaleApproval, notifies the owner.
    """
    user_profile = get_user_profile(request)
    if not user_profile:
        return redirect('home')

    item = get_object_or_404(Item, id=item_id, store__business=user_profile.business)

    if not item.is_restricted:
        messages.error(request, _('This item does not require approval.'))
        return redirect('add_transaction')

    quantity = int(request.POST.get('quantity', 0))
    recipient = request.POST.get('recipient', '')
    invoice_no = request.POST.get('invoice_no', '')
    payment_method = request.POST.get('payment_method', 'cash')

    if quantity <= 0:
        messages.error(request, _('Please enter a valid quantity.'))
        return redirect('add_transaction')

    if item.current_balance() < quantity:
        messages.error(
            request,
            _('Not enough stock. Available: %(bal)s %(unit)s.')
            % {'bal': item.current_balance(), 'unit': item.unit}
        )
        return redirect('add_transaction')

    approval = _create_approval_request(
        request, item, user_profile,
        quantity=quantity,
        recipient=recipient,
        invoice_no=invoice_no,
        payment_method=payment_method,
    )

    return render(request, 'core/sale_approval_pending.html', {
        'approval': approval,
        'item': item,
    })


@login_required
@owner_required
def pending_approvals(request):
    """Owner's view of all pending sale approval requests."""
    user_profile = get_user_profile(request)
    business = user_profile.business

    pending = ItemSaleApproval.objects.filter(
        business=business, status='pending'
    ).select_related('item', 'requested_by')

    history = ItemSaleApproval.objects.filter(
        business=business, status__in=['approved', 'denied']
    ).select_related('item', 'requested_by', 'decided_by')[:20]

    return render(request, 'core/pending_approvals.html', {
        'pending': pending,
        'history': history,
        'today': timezone.now().date().strftime('%B %d, %Y'),
    })


@login_required
@owner_required
@require_POST
def decide_approval(request, approval_id):
    """Owner approves or denies a sale approval request."""
    user_profile = get_user_profile(request)
    approval = get_object_or_404(
        ItemSaleApproval, id=approval_id,
        business=user_profile.business, status='pending'
    )

    decision = request.POST.get('decision')  # 'approve' or 'deny'
    denial_reason = request.POST.get('denial_reason', '').strip()

    if decision == 'approve':
        txn = Transaction.objects.create(
            item=approval.item,
            type='Issue',
            qty=-approval.quantity,
            recipient=approval.recipient,
            invoice_no=approval.invoice_no,
            business=approval.business,
            payment_method=approval.payment_method,
        )
        approval.status = 'approved'
        approval.transaction = txn
        approval.decided_by = request.user
        approval.decided_at = timezone.now()
        approval.save()

        Notification.objects.create(
            user=approval.requested_by,
            title=f'✅ Sale approved — {approval.item.description}',
            message=(
                f'Your request to sell {approval.quantity} {approval.item.unit} '
                f'of {approval.item.description} has been approved. '
                f'The transaction has been recorded automatically.'
            ),
            notification_type='info',
        )

        messages.success(
            request,
            _(f'Approved. {approval.quantity} {approval.item.unit} of '
              f'{approval.item.description} sold and stock updated.')
        )

    elif decision == 'deny':
        approval.status = 'denied'
        approval.denial_reason = denial_reason
        approval.decided_by = request.user
        approval.decided_at = timezone.now()
        approval.save()

        Notification.objects.create(
            user=approval.requested_by,
            title=f'❌ Sale denied — {approval.item.description}',
            message=(
                f'Your request to sell {approval.quantity} {approval.item.unit} '
                f'of {approval.item.description} was denied by the owner.'
                f'{" Reason: " + denial_reason if denial_reason else ""}'
            ),
            notification_type='warning',
        )

        messages.info(request, _(f'Sale request denied.'))

    return redirect('pending_approvals')


@login_required
def approval_status(request, approval_id):
    """
    AJAX endpoint — staff polls this to check if decision was made.
    Returns JSON with status and message.
    """
    user_profile = get_user_profile(request)
    approval = get_object_or_404(
        ItemSaleApproval, id=approval_id,
        business=user_profile.business,
        requested_by=request.user,
    )
    return JsonResponse({
        'status': approval.status,
        'message': approval.denial_reason if approval.status == 'denied' else '',
    })
