from django.db import models
from django.contrib.auth.models import User

# Create your models here.

class AppUser(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='app_user')
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    profile_name = models.CharField(max_length=100, unique=True)
    signup_date = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.profile_name} (ID: {self.id})"

class PostType(models.Model):
    id = models.AutoField(primary_key=True)
    post_type_name = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return self.post_type_name


class Post(models.Model):
    id = models.AutoField(primary_key=True)
    created_by_user = models.ForeignKey(AppUser, on_delete=models.CASCADE)
    created_datetime = models.DateTimeField(auto_now_add=True)
    caption = models.TextField(blank=True)
    post_type = models.ForeignKey(PostType, on_delete=models.SET_NULL, null=True)

    def __str__(self):
        return f"Post {self.id} by {self.created_by_user.profile_name}"


class Follower(models.Model):
    following_user = models.ForeignKey(AppUser, related_name='following', on_delete=models.CASCADE)
    followed_user = models.ForeignKey(AppUser, related_name='followers', on_delete=models.CASCADE)

    class Meta:
        unique_together = (('following_user', 'followed_user'),)


class Reaction(models.Model):
    user = models.ForeignKey(AppUser, on_delete=models.CASCADE)
    post = models.ForeignKey(Post, on_delete=models.CASCADE)

    class Meta:
        unique_together = (('user', 'post'),)


class Comment(models.Model):
    id = models.AutoField(primary_key=True)
    created_by_user = models.ForeignKey(AppUser, on_delete=models.CASCADE)
    post = models.ForeignKey(Post, on_delete=models.CASCADE)
    created_datetime = models.DateTimeField(auto_now_add=True)
    comment = models.TextField()
    comment_replied_to = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='replies')


class Filter(models.Model):
    id = models.AutoField(primary_key=True)
    filter_name = models.CharField(max_length=100)
    filter_details = models.TextField(blank=True)

    def __str__(self):
        return self.filter_name


class PostMedia(models.Model):
    id = models.AutoField(primary_key=True)
    post = models.ForeignKey(Post, on_delete=models.CASCADE)
    filter = models.ForeignKey(Filter, on_delete=models.SET_NULL, null=True)
    media_file = models.FileField(upload_to='media/')
    position = models.IntegerField()
    longitude = models.FloatField(null=True, blank=True)
    latitude = models.FloatField(null=True, blank=True)


class PostMediaUserTag(models.Model):
    post_media = models.ForeignKey(PostMedia, on_delete=models.CASCADE)
    user = models.ForeignKey(AppUser, on_delete=models.CASCADE)
    x_coordinate = models.FloatField()
    y_coordinate = models.FloatField()

    class Meta:
        unique_together = (('post_media', 'user'),)


class Effect(models.Model):
    id = models.AutoField(primary_key=True)
    effect_name = models.CharField(max_length=100)

    def __str__(self):
        return self.effect_name


class PostEffect(models.Model):
    post_media = models.ForeignKey(PostMedia, on_delete=models.CASCADE)
    effect = models.ForeignKey(Effect, on_delete=models.CASCADE)
    scale = models.FloatField()
    