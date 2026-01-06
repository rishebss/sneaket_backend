from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from cloudinary.models import CloudinaryField  # Add this import

class Sneaker(models.Model):
    # Category choices
    CATEGORY_CHOICES = [
        ('running', 'Running'),
        ('basketball', 'Basketball'),
        ('lifestyle', 'Lifestyle'),
        ('training', 'Training'),
        ('skateboarding', 'Skateboarding'),
        ('soccer', 'Soccer'),
        ('boots', 'Boots'),
        ('customs', 'Customs'),
    ]
    
    # Brand choices
    BRAND_CHOICES = [
        ('nike', 'Nike'),
        ('adidas', 'Adidas'),
        ('jordan', 'Jordan'),
        ('puma', 'Puma'),
        ('new_balance', 'New Balance'),
        ('reebok', 'Reebok'),
        ('converse', 'Converse'),
        ('vans', 'Vans'),
        ('balenciaga', 'Balenciaga'),
        ('gucci', 'Gucci'),
        ('other', 'Other'),
    ]
    
    # Size choices (US sizes)
    SIZE_CHOICES = [
        ('5', 'US 5'),
        ('5.5', 'US 5.5'),
        ('6', 'US 6'),
        ('6.5', 'US 6.5'),
        ('7', 'US 7'),
        ('7.5', 'US 7.5'),
        ('8', 'US 8'),
        ('8.5', 'US 8.5'),
        ('9', 'US 9'),
        ('9.5', 'US 9.5'),
        ('10', 'US 10'),
        ('10.5', 'US 10.5'),
        ('11', 'US 11'),
        ('11.5', 'US 11.5'),
        ('12', 'US 12'),
        ('13', 'US 13'),
    ]
    
    # Feature tags
    FEATURE_CHOICES = [
        ('best_seller', 'Best Seller'),
        ('featured', 'Featured'),
        ('new_arrival', 'New Arrival'),
        ('value_for_money', 'Value for Money'),
        ('limited_edition', 'Limited Edition'),
        ('ai_designed', 'AI Designed'),
        ('trending', 'Trending'),
        ('staff_pick', 'Staff Pick'),
    ]
    
    # Basic Information
    name = models.CharField(max_length=200)
    brand = models.CharField(max_length=50, choices=BRAND_CHOICES)
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES)
    
    # Pricing & Inventory
    price = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    original_price = models.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        validators=[MinValueValidator(0)],
        null=True, 
        blank=True
    )
    copies = models.IntegerField(default=1, validators=[MinValueValidator(0)])
    
    # Images - UPDATED FOR CLOUDINARY
    img1 = CloudinaryField('image', folder='sneaket/sneakers/')
    img2 = CloudinaryField('image', folder='sneaket/sneakers/', null=True, blank=True)
    img3 = CloudinaryField('image', folder='sneaket/sneakers/', null=True, blank=True)
    
    # Description
    description = models.TextField()
    short_description = models.CharField(max_length=200, blank=True)
    
    # Sizes
    sizes = models.CharField(max_length=10, choices=SIZE_CHOICES)
    available_sizes = models.JSONField(
        default=list,
        help_text='List of available sizes in JSON format like ["8", "9", "10"]'
    )
    
    # Features/Tags
    features = models.JSONField(
        default=list,
        help_text='List of feature tags like ["best_seller", "featured"]'
    )
    
    
    # Ratings
    rating = models.DecimalField(
        max_digits=3, 
        decimal_places=2, 
        validators=[MinValueValidator(0), MaxValueValidator(5)],
        null=True, 
        blank=True
    )
    review_count = models.IntegerField(default=0)
    

    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.brand} - {self.name}"
    
    def save(self, *args, **kwargs):
        if not self.original_price:
            self.original_price = self.price
        if not self.available_sizes:
            self.available_sizes = [self.sizes]
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