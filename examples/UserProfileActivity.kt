package com.example.app

import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import kotlinx.coroutines.GlobalScope
import kotlinx.coroutines.launch

class UserProfileActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_profile)

        // Load the user profile when the screen opens.
        GlobalScope.launch {
            val user = api.getUser(userId)
            nameTextView.text = user.name
            avatarView.load(user.avatarUrl)
        }
    }
}
