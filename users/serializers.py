from rest_framework import serializers
from .models import UserProfile
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth import authenticate


class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model=UserProfile
        fields='__all__'
        read_only_fields = ['created_at', 'updated_at', 'user']


class UserSerializer(serializers.ModelSerializer):
    profile=UserProfileSerializer(read_only=True)
    password = serializers.CharField(write_only=True, required=True, validators=[validate_password])

    class Meta:
        model=User
        fields=['id','username','first_name','last_name','email','password','profile']

        extra_kwargs = {
            'first_name': {'required': True},
            'last_name': {'required': True},
            'email': {'required': True},
        }

    def create(self, validated_data):
       
        user = User.objects.create_user(
            username=validated_data['username'],
            first_name=validated_data['first_name'],
            last_name=validated_data['last_name'],
            email=validated_data['email'],
            password=validated_data['password'],  
        )
        return user



class UserUpdateSerializer(serializers.ModelSerializer):
    profile = UserProfileSerializer(required=False)
    
    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email', 'profile']
    
    def update(self, instance, validated_data):
        profile_data = validated_data.pop('profile', None)
        
        # Update User fields
        instance.first_name = validated_data.get('first_name', instance.first_name)
        instance.last_name = validated_data.get('last_name', instance.last_name)
        instance.email = validated_data.get('email', instance.email)
        instance.save()
        
        # Update UserProfile fields
        if profile_data:
            profile = instance.profile
            profile.phone = profile_data.get('phone', profile.phone)
            profile.address = profile_data.get('address', profile.address)
            profile.pincode = profile_data.get('pincode', profile.pincode)
            profile.state = profile_data.get('state', profile.state)
            profile.city = profile_data.get('city', profile.city)
            profile.save()
        
        return instance


class LoginSerializer(serializers.Serializer):
    username_or_email = serializers.CharField() 
    password = serializers.CharField(write_only=True)      


    def validate(self, data):
        username_or_email = data.get('username_or_email')
        password = data.get('password')
        
        user = None
        
        # Check if input is email or username
        if '@' in username_or_email:
            try:
                user_obj = User.objects.get(email=username_or_email)
                # Use username for authentication (Django auth uses username)
                user = authenticate(username=user_obj.username, password=password)
            except User.DoesNotExist:
                raise serializers.ValidationError("Invalid email or password")
        else:
            # It's a username
            user = authenticate(username=username_or_email, password=password)
        
        if not user:
            raise serializers.ValidationError("Invalid email/username or password")
        
        if not user.is_active:
            raise serializers.ValidationError("Account is disabled")
        
        data['user'] = user
        return data      
