from django.db import models


class Token(models.Model):
    """
    Mirrors the `tokens` table used by the Flask app.
    Managed = False means Django won't try to create/alter this table —
    it already exists in the MySQL DB created by the Flask app.
    Set managed = True if you want Django migrations to own it.
    """
    token      = models.CharField(max_length=512)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'tokens'
        managed  = False        # table already exists — flip to True for a fresh DB
        ordering = ['-created_at']

    def __str__(self):
        return self.token[:20] + '…'
