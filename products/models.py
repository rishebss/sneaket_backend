from cloudinary.models import CloudinaryField  # Add this import
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Avg
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.conf import settings


class Sneaker(models.Model):
    # Category choices
    CATEGORY_CHOICES = [
        ("running", "Running"),
        ("basketball", "Basketball"),
        ("lifestyle", "Lifestyle"),
        ("training", "Training"),
        ("skateboarding", "Skateboarding"),
        ("soccer", "Soccer"),
        ("boots", "Boots"),
        ("customs", "Customs"),
    ]

    # Brand choices
    BRAND_CHOICES = [
        ("nike", "Nike"),
        ("adidas", "Adidas"),
        ("jordan", "Jordan"),
        ("puma", "Puma"),
        ("new_balance", "New Balance"),
        ("reebok", "Reebok"),
        ("converse", "Converse"),
        ("vans", "Vans"),
        ("balenciaga", "Balenciaga"),
        ("gucci", "Gucci"),
        ("other", "Other"),
    ]

    # Size choices (US sizes)
    SIZE_CHOICES = [
        ("5", "US 5"),
        ("5.5", "US 5.5"),
        ("6", "US 6"),
        ("6.5", "US 6.5"),
        ("7", "US 7"),
        ("7.5", "US 7.5"),
        ("8", "US 8"),
        ("8.5", "US 8.5"),
        ("9", "US 9"),
        ("9.5", "US 9.5"),
        ("10", "US 10"),
        ("10.5", "US 10.5"),
        ("11", "US 11"),
        ("11.5", "US 11.5"),
        ("12", "US 12"),
        ("13", "US 13"),
    ]

    # Feature tags
    FEATURE_CHOICES = [
        ("best_seller", "Best Seller"),
        ("featured", "Featured"),
        ("new_arrival", "New Arrival"),
        ("value_for_money", "Value for Money"),
        ("limited_edition", "Limited Edition"),
        ("ai_designed", "AI Designed"),
        ("trending", "Trending"),
        ("staff_pick", "Staff Pick"),
    ]

    # Basic Information
    name = models.CharField(max_length=200)
    brand = models.CharField(max_length=50, choices=BRAND_CHOICES)
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES)

    # Pricing & Inventory
    price = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(0)]
    )
    original_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
        null=True,
        blank=True,
    )
    copies = models.IntegerField(default=1, validators=[MinValueValidator(0)])

    # Images - UPDATED FOR CLOUDINARY
    img1 = CloudinaryField("image", folder="sneaket/sneakers/")
    img2 = CloudinaryField("image", folder="sneaket/sneakers/", null=True, blank=True)
    img3 = CloudinaryField("image", folder="sneaket/sneakers/", null=True, blank=True)

    # Description
    description = models.TextField()
    short_description = models.CharField(max_length=200, blank=True)

    # Sizes

    available_sizes = models.JSONField(
        default=list,
        help_text='List of available sizes in JSON format like ["8", "9", "10"]',
    )

    # Features/Tags
    features = models.JSONField(
        default=list, help_text='List of feature tags like ["best_seller", "featured"]'
    )

    # Ratings
    rating = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(5)],
        null=True,
        blank=True,
    )
    review_count = models.IntegerField(default=0)

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.brand} - {self.name}"

    def save(self, *args, **kwargs):
        if not self.original_price:
            self.original_price = self.price
        super().save(*args, **kwargs)

    @property
    def discount_percentage(self):
        if self.original_price and self.original_price > self.price:
            discount = ((self.original_price - self.price) / self.original_price) * 100
            return round(discount, 2)
        return 0

    @property
    def in_stock(self):
        return self.copies > 0

    @property
    def image_list(self):
        images = [self.img1.url]
        if self.img2:
            images.append(self.img2.url)
        if self.img3:
            images.append(self.img3.url)
        return images


class Favorite(models.Model):
    """
    Simple favorite model - just user and sneaker
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="favorites"
    )
    sneaker = models.ForeignKey(
        Sneaker, on_delete=models.CASCADE, related_name="favorited_by"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # Ensure a user can only favorite a sneaker once
        unique_together = ["user", "sneaker"]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.username} - {self.sneaker.name}"


class CartItem(models.Model):
    """
    Cart item - links a user to a sneaker they want to buy.
    User and Sneaker are ForeignKeys; quantity + selected size are tracked.
    A user can only have one line per sneaker+size combination.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cart_items"
    )
    sneaker = models.ForeignKey(
        Sneaker, on_delete=models.CASCADE, related_name="in_carts"
    )
    size = models.CharField(
        max_length=10,
        choices=Sneaker.SIZE_CHOICES,
        null=True,
        blank=True,
        help_text="Selected US size for this cart line",
    )
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    is_selected = models.BooleanField(
        default=True, help_text="Whether this line is selected for checkout"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # One line per user + sneaker + size
        unique_together = ["user", "sneaker", "size"]
        ordering = ["-created_at"]

    def __str__(self):
        size_label = f" (US {self.size})" if self.size else ""
        return (
            f"{self.user.username} - {self.sneaker.name}{size_label} x{self.quantity}"
        )


class Review(models.Model):
    """
    User-submitted product review. One review per user per sneaker.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="reviews",
    )
    sneaker = models.ForeignKey(
        Sneaker, on_delete=models.CASCADE, related_name="reviews"
    )
    rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    title = models.CharField(max_length=120, blank=True)
    comment = models.TextField(max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        unique_together = ["user", "sneaker"]

    def __str__(self):
        return f"{self.user.username} → {self.sneaker.name} ({self.rating}★)"


def _update_sneaker_rating(sneaker):
    """Recompute the sneaker's cached average rating + review count."""
    agg = Review.objects.filter(sneaker=sneaker).aggregate(
        avg=Avg("rating"), count=models.Count("id")
    )
    sneaker.review_count = agg["count"] or 0
    sneaker.rating = round(agg["avg"], 2) if agg["avg"] else None
    sneaker.save(update_fields=["review_count", "rating", "updated_at"])


@receiver(post_save, sender=Review)
def _review_saved(sender, instance, **kwargs):
    _update_sneaker_rating(instance.sneaker)


@receiver(post_delete, sender=Review)
def _review_deleted(sender, instance, **kwargs):
    _update_sneaker_rating(instance.sneaker)
