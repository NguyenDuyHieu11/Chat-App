from rest_framework import serializers
from django.contrib.auth.models import User
from .models import (
    UserProfile, Follow, Post, Like, Comment, CommentLike, 
    Feed, Hashtag, Mention
)


class UserSerializer(serializers.ModelSerializer):
    """Serializer for User model"""
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 'date_joined']
        read_only_fields = ['id', 'date_joined']


class UserProfileSerializer(serializers.ModelSerializer):
    """Serializer for UserProfile model"""
    user = UserSerializer(read_only=True)
    avatar_url = serializers.SerializerMethodField()
    
    class Meta:
        model = UserProfile
        fields = [
            'user', 'bio', 'avatar', 'avatar_url', 'location', 'website',
            'followers_count', 'following_count', 'created_at', 'updated_at'
        ]
        read_only_fields = ['followers_count', 'following_count', 'created_at', 'updated_at']

    def get_avatar_url(self, obj):
        if obj.avatar:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.avatar.url)
        return None


class FollowSerializer(serializers.ModelSerializer):
    """Serializer for Follow relationships"""
    follower = UserSerializer(read_only=True)
    following = UserSerializer(read_only=True)
    
    class Meta:
        model = Follow
        fields = ['follower', 'following', 'created_at']
        read_only_fields = ['created_at']


class HashtagSerializer(serializers.ModelSerializer):
    """Serializer for Hashtag model"""
    class Meta:
        model = Hashtag
        fields = ['id', 'name', 'usage_count', 'created_at']
        read_only_fields = ['usage_count', 'created_at']


class MentionSerializer(serializers.ModelSerializer):
    """Serializer for Mention model"""
    mentioned_user = UserSerializer(read_only=True)
    
    class Meta:
        model = Mention
        fields = ['mentioned_user', 'created_at']
        read_only_fields = ['created_at']


class CommentSerializer(serializers.ModelSerializer):
    """Serializer for Comment model"""
    author = UserSerializer(read_only=True)
    replies = serializers.SerializerMethodField()
    is_liked = serializers.SerializerMethodField()
    
    class Meta:
        model = Comment
        fields = [
            'id', 'author', 'content', 'likes_count', 'created_at', 
            'updated_at', 'is_deleted', 'parent', 'replies', 'is_liked'
        ]
        read_only_fields = ['id', 'author', 'likes_count', 'created_at', 'updated_at']

    def get_replies(self, obj):
        if obj.replies.exists():
            return CommentSerializer(
                obj.replies.filter(is_deleted=False), 
                many=True, 
                context=self.context
            ).data
        return []

    def get_is_liked(self, obj):
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            return CommentLike.objects.filter(
                user=request.user, 
                comment=obj
            ).exists()
        return False


class PostSerializer(serializers.ModelSerializer):
    """Serializer for Post model"""
    author = UserSerializer(read_only=True)
    hashtags = HashtagSerializer(many=True, read_only=True)
    mentions = MentionSerializer(many=True, read_only=True)
    comments = serializers.SerializerMethodField()
    is_liked = serializers.SerializerMethodField()
    image_url = serializers.SerializerMethodField()
    video_url = serializers.SerializerMethodField()
    
    class Meta:
        model = Post
        fields = [
            'id', 'author', 'content', 'post_type', 'image', 'image_url',
            'video', 'video_url', 'link_url', 'link_title', 'link_description',
            'likes_count', 'comments_count', 'shares_count', 'created_at',
            'updated_at', 'is_deleted', 'hashtags', 'mentions', 'comments', 'is_liked'
        ]
        read_only_fields = [
            'id', 'author', 'likes_count', 'comments_count', 'shares_count',
            'created_at', 'updated_at', 'hashtags', 'mentions'
        ]

    def get_comments(self, obj):
        # Only return top-level comments (not replies)
        top_comments = obj.comments.filter(parent=None, is_deleted=False)[:5]
        return CommentSerializer(top_comments, many=True, context=self.context).data

    def get_is_liked(self, obj):
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            return Like.objects.filter(user=request.user, post=obj).exists()
        return False

    def get_image_url(self, obj):
        if obj.image:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.image.url)
        return None

    def get_video_url(self, obj):
        if obj.video:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.video.url)
        return None

    def create(self, validated_data):
        """Create post and extract hashtags/mentions"""
        validated_data['author'] = self.context['request'].user
        post = super().create(validated_data)
        
        # Extract and create hashtags
        self._extract_hashtags(post)
        
        # Extract and create mentions
        self._extract_mentions(post)
        
        return post

    def update(self, instance, validated_data):
        """Update post and re-extract hashtags/mentions"""
        post = super().update(instance, validated_data)
        
        # Clear existing hashtags and mentions
        post.hashtags.clear()
        post.mentions.all().delete()
        
        # Re-extract hashtags and mentions
        self._extract_hashtags(post)
        self._extract_mentions(post)
        
        return post

    def _extract_hashtags(self, post):
        """Extract hashtags from post content"""
        import re
        hashtag_pattern = r'#(\w+)'
        hashtags = re.findall(hashtag_pattern, post.content)
        
        for tag in hashtags:
            hashtag, created = Hashtag.objects.get_or_create(
                name=tag.lower(),
                defaults={'usage_count': 0}
            )
            hashtag.usage_count += 1
            hashtag.save()
            post.hashtags.add(hashtag)

    def _extract_mentions(self, post):
        """Extract user mentions from post content"""
        import re
        mention_pattern = r'@(\w+)'
        mentions = re.findall(mention_pattern, post.content)
        
        for username in mentions:
            try:
                user = User.objects.get(username=username)
                Mention.objects.get_or_create(
                    post=post,
                    mentioned_user=user
                )
            except User.DoesNotExist:
                continue


class PostCreateSerializer(serializers.ModelSerializer):
    """Simplified serializer for creating posts"""
    class Meta:
        model = Post
        fields = ['content', 'post_type', 'image', 'video', 'link_url', 'link_title', 'link_description']

    def create(self, validated_data):
        validated_data['author'] = self.context['request'].user
        return super().create(validated_data)


class LikeSerializer(serializers.ModelSerializer):
    """Serializer for Like model"""
    user = UserSerializer(read_only=True)
    
    class Meta:
        model = Like
        fields = ['user', 'created_at']
        read_only_fields = ['user', 'created_at']


class FeedSerializer(serializers.ModelSerializer):
    """Serializer for Feed entries"""
    post = PostSerializer(read_only=True)
    
    class Meta:
        model = Feed
        fields = ['post', 'score', 'created_at', 'is_seen']
        read_only_fields = ['score', 'created_at']


class UserRegistrationSerializer(serializers.ModelSerializer):
    """Serializer for user registration"""
    password = serializers.CharField(write_only=True, min_length=8)
    password_confirm = serializers.CharField(write_only=True)
    
    class Meta:
        model = User
        fields = ['username', 'email', 'password', 'password_confirm', 'first_name', 'last_name']

    def validate(self, data):
        if data['password'] != data['password_confirm']:
            raise serializers.ValidationError("Passwords don't match")
        return data

    def create(self, validated_data):
        validated_data.pop('password_confirm')
        password = validated_data.pop('password')
        user = User.objects.create_user(**validated_data)
        user.set_password(password)
        user.save()
        
        # Create user profile
        UserProfile.objects.create(user=user)
        
        return user


class UserUpdateSerializer(serializers.ModelSerializer):
    """Serializer for updating user information"""
    profile = UserProfileSerializer(read_only=True)
    
    class Meta:
        model = User
        fields = ['username', 'email', 'first_name', 'last_name', 'profile']
        read_only_fields = ['username']  # Username shouldn't be changeable


class PasswordChangeSerializer(serializers.Serializer):
    """Serializer for password change"""
    old_password = serializers.CharField(required=True)
    new_password = serializers.CharField(required=True, min_length=8)
    new_password_confirm = serializers.CharField(required=True)

    def validate(self, data):
        if data['new_password'] != data['new_password_confirm']:
            raise serializers.ValidationError("New passwords don't match")
        return data

    def validate_old_password(self, value):
        user = self.context['request'].user
        if not user.check_password(value):
            raise serializers.ValidationError("Old password is incorrect")
        return value