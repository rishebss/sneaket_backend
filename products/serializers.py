from rest_framework import serializers
from .models import Sneaker

class SneakerSerializer(serializers.ModelSerializer):
    # Override the image fields to return URLs directly
    img1 = serializers.SerializerMethodField()
    img2 = serializers.SerializerMethodField()
    img3 = serializers.SerializerMethodField()
    
    class Meta:
        model = Sneaker
        fields = '__all__'
        read_only_fields = ['created_at', 'updated_at']
    
    def get_img1(self, obj):
        return obj.img1.url if obj.img1 else None
    
    def get_img2(self, obj):
        return obj.img2.url if obj.img2 else None
    
    def get_img3(self, obj):
        return obj.img3.url if obj.img3 else None