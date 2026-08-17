from django.contrib import admin
from .models import Order, OrderItem, PendingCheckout


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
        "created_at",
        "updated_at",
    )
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
