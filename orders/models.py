from datetime import timedelta

from django.db import models
from django.conf import settings
from django.utils import timezone


def add_working_days(start_date, working_days):
    """Return the date `working_days` business days after `start_date` (excludes weekends)."""
    current = start_date
    count = 0
    while count < working_days:
        current += timedelta(days=1)
        if current.weekday() < 5:  # Monday=0 ... Friday=4
            count += 1
    return current


class Order(models.Model):
    STATUS_CHOICES = [
        ("pending_payment", "Pending Payment"),
        ("confirmed", "Confirmed"),
        ("processing", "Processing"),
        ("shipped", "Shipped"),
        ("delivered", "Delivered"),
        ("delivery", "Delivery"),
        ("cancellation_requested", "Cancellation Requested"),
        ("cancellation_approved", "Cancellation Approved"),
        ("cancelled", "Cancelled"),
        ("refunded", "Refunded"),
    ]
    PAYMENT_METHOD_CHOICES = [
        ("cod", "Cash on Delivery"),
        ("online", "Online (Razorpay)"),
        ("wallet", "Wallet"),
    ]
    PAYMENT_STATUS_CHOICES = [
        ("pending", "Pending"),
        ("paid", "Paid"),
        ("failed", "Failed"),
        ("refunded", "Refunded"),
    ]

    # Identity
    order_number = models.CharField(max_length=50, unique=True, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="orders",
    )

    # Shipping snapshot (copied at order time so history stays correct)
    recipient_name = models.CharField(max_length=255)
    email = models.EmailField()
    phone = models.CharField(max_length=20, blank=True)
    address = models.TextField()
    pincode = models.CharField(max_length=10, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)

    # Payment
    payment_method = models.CharField(
        max_length=10, choices=PAYMENT_METHOD_CHOICES, default="online"
    )
    payment_status = models.CharField(
        max_length=10, choices=PAYMENT_STATUS_CHOICES, default="pending"
    )
    status = models.CharField(
        max_length=30, choices=STATUS_CHOICES, default="pending_payment"
    )

    # Pricing snapshot
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    shipping_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # Razorpay references
    razorpay_order_id = models.CharField(max_length=100, blank=True, null=True)
    razorpay_payment_id = models.CharField(max_length=100, blank=True, null=True)
    razorpay_signature = models.CharField(max_length=255, blank=True, null=True)

    # Cart lines cleared after a successful payment (only the selected ones)
    cart_item_ids = models.JSONField(default=list, blank=True)

    # Cancellation flow
    cancellation_requested_at = models.DateTimeField(null=True, blank=True)
    cancellation_approved_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True, default="")

    # Delivery
    delivery_date = models.DateField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if self._state.adding and not self.delivery_date:
            self.delivery_date = add_working_days(timezone.localdate(), 7)
        super().save(*args, **kwargs)

    def get_effective_status(self):
        """
        Auto-progress a confirmed order based on time since it was placed:
          - day 4+ -> shipped (cancel window closed)
          - day 7+ -> delivery (out for delivery)
        """
        if self.status == "confirmed":
            days = (timezone.localdate() - self.created_at.date()).days
            if days >= 7:
                return "delivery"
            if days >= 4:
                return "shipped"
        return self.status

    def __str__(self):
        return f"{self.order_number} - {self.user.username}"


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    sneaker = models.ForeignKey(
        "products.Sneaker", on_delete=models.PROTECT, related_name="order_items"
    )
    # Snapshot of product data at purchase time
    sneaker_name = models.CharField(max_length=200)
    sneaker_image = models.URLField(blank=True)
    size = models.CharField(max_length=10, blank=True)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    line_total = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f"{self.sneaker_name} x{self.quantity}"


class PendingCheckout(models.Model):
    """
    Transient snapshot of a checkout that has a Razorpay order created but is
    NOT yet a registered Order. Promoted to an Order only after the payment
    signature is verified. Never shown in "My Orders" or admin.
    """

    razorpay_order_id = models.CharField(max_length=100, unique=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="pending_checkouts",
    )
    amount = models.IntegerField(help_text="Amount in paise")
    snapshot = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Pending {self.razorpay_order_id} - {self.user.username}"
