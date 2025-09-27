from django.contrib import admin
from django.utils.html import format_html
from .models import (
    UserProfile, Follow, Post, Like, Comment, CommentLike,
    Feed, Hashtag, Mention
)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'followers_count', 'following_count', 'created_at']
    list_filter = ['created_at']
    search_fields = ['user__username', 'user__email', 'bio']
    readonly_fields = ['followers_count', 'following_count', 'created_at', 'updated_at']
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('user')


@admin.register(Follow)
class FollowAdmin(admin.ModelAdmin):
    list_display = ['follower', 'following', 'created_at']
    list_filter = ['created_at']
    search_fields = ['follower__username', 'following__username']
    raw_id_fields = ['follower', 'following']


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ['id', 'author', 'content_preview', 'post_type', 'likes_count', 'comments_count', 'created_at']
    list_filter = ['post_type', 'created_at', 'is_deleted', 'is_reported']
    search_fields = ['content', 'author__username']
    readonly_fields = ['likes_count', 'comments_count', 'shares_count', 'created_at', 'updated_at']
    raw_id_fields = ['author']
    
    def content_preview(self, obj):
        return obj.content[:50] + '...' if len(obj.content) > 50 else obj.content
    content_preview.short_description = 'Content Preview'
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('author')


@admin.register(Like)
class LikeAdmin(admin.ModelAdmin):
    list_display = ['user', 'post', 'created_at']
    list_filter = ['created_at']
    search_fields = ['user__username', 'post__content']
    raw_id_fields = ['user', 'post']


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ['id', 'author', 'post', 'content_preview', 'likes_count', 'created_at']
    list_filter = ['created_at', 'is_deleted']
    search_fields = ['content', 'author__username', 'post__content']
    readonly_fields = ['likes_count', 'created_at', 'updated_at']
    raw_id_fields = ['author', 'post', 'parent']
    
    def content_preview(self, obj):
        return obj.content[:50] + '...' if len(obj.content) > 50 else obj.content
    content_preview.short_description = 'Content Preview'
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('author', 'post')


@admin.register(CommentLike)
class CommentLikeAdmin(admin.ModelAdmin):
    list_display = ['user', 'comment', 'created_at']
    list_filter = ['created_at']
    search_fields = ['user__username', 'comment__content']
    raw_id_fields = ['user', 'comment']


@admin.register(Feed)
class FeedAdmin(admin.ModelAdmin):
    list_display = ['user', 'post', 'score', 'is_seen', 'created_at']
    list_filter = ['is_seen', 'created_at']
    search_fields = ['user__username', 'post__content']
    readonly_fields = ['created_at']
    raw_id_fields = ['user', 'post']


@admin.register(Hashtag)
class HashtagAdmin(admin.ModelAdmin):
    list_display = ['name', 'usage_count', 'created_at']
    list_filter = ['created_at']
    search_fields = ['name']
    readonly_fields = ['usage_count', 'created_at']
    filter_horizontal = ['posts']


@admin.register(Mention)
class MentionAdmin(admin.ModelAdmin):
    list_display = ['post', 'mentioned_user', 'created_at']
    list_filter = ['created_at']
    search_fields = ['mentioned_user__username', 'post__content']
    raw_id_fields = ['post', 'mentioned_user']