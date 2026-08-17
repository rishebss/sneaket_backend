from django.contrib import admin
from django.db import transaction
from django.utils import timezone

from products.models import Sneaker
from .models import Order, OrderItem, PendingCheckout


@admin.action(description="Approve cancellation & restock inventory")
def approve_cancellation(modeladmin, request, queryset):
    for order in queryset.filter(status="cancellation_requested"):
        with transaction.atomic():
            for item in order.items.select_related("sneaker").select_for_update():
                sneaker = Sneaker.objects.select_for_update().get(id=item.sneaker_id)
                sneaker.copies = sneaker.copies + item.quantity
                sneaker.save(update_fields=["copies", "updated_at"])
            order.status = "cancellation_approved"
            order.cancellation_approved_at = timezone.now()
            if order.payment_status == "paid":
                order.payment_status = "refunded"
            order.save(
                update_fields=[
                    "status",
                    "cancellation_approved_at",
                    "payment_status",
                    "updated_at",
                ]
            )


@admin.action(description="Deny cancellation request")
def deny_cancellation(modeladmin, request, queryset):
    for order in queryset.filter(status="cancellation_requested"):
        order.status = "confirmed"
        order.cancellation_reason = ""
        order.save(update_fields=["status", "cancellation_reason", "updated_at"])


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    readonly_fields = (
        "sneaker",
        "sneaker_name",
        "sneaker_image",
        "size",
        "quantity",
        "unit_price",
        "line_total",
    )
    extra = 0


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        "order_number",
        "user",
        "status",
        "payment_status",
        "payment_method",
        "total",
        "created_at",
    )
    list_filter = ("status", "payment_status", "payment_method", "created_at")
    search_fields = ("order_number", "user__username", "email", "recipient_name")
    readonly_fields = (
        "order_number",
        "user",
        "razorpay_order_id",
        "razorpay_payment_id",
        "razorpay_signature",
        "cart_item_ids",
        "subtotal",
        "shipping_fee",
        "total",
        "cancellation_requested_at",
        "cancellation_approved_at",
        "created_at",
        "updated_at",
    )
    actions = [approve_cancellation, deny_cancellation]
    inlines = [OrderItemInline]
    ordering = ("-created_at",)


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "order",
        "sneaker_name",
        "size",
        "quantity",
        "unit_price",
        "line_total",
    )
    list_filter = ("sneaker",)
    search_fields = ("sneaker_name", "order__order_number")
    readonly_fields = (
        "order",
        "sneaker",
        "sneaker_name",
        "sneaker_image",
        "size",
        "quantity",
        "unit_price",
        "line_total",
    )


@admin.register(PendingCheckout)
class PendingCheckoutAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "razorpay_order_id",
        "amount",
        "created_at",
    )
    list_filter = ("created_at",)
    search_fields = ("razorpay_order_id", "user__username")
    readonly_fields = ("user", "razorpay_order_id", "amount", "snapshot")
