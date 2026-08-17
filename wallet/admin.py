from django.contrib import admin
from .models import Wallet, WalletTransaction, WalletTopUp


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ("user", "balance", "updated_at")
    search_fields = ("user__username",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(WalletTransaction)
class WalletTransactionAdmin(admin.ModelAdmin):
    list_display = ("id", "wallet", "type", "amount", "reason", "created_at")
    list_filter = ("type", "reason")
    search_fields = ("reference", "reason", "wallet__user__username")
    readonly_fields = ("wallet", "type", "amount", "reason", "reference", "created_at")


@admin.register(WalletTopUp)
class WalletTopUpAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "razorpay_order_id", "amount", "created_at")
    search_fields = ("razorpay_order_id", "user__username")
    readonly_fields = ("user", "razorpay_order_id", "amount")
