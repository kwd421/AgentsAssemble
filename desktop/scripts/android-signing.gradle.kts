import com.android.build.api.dsl.ApplicationExtension
import org.gradle.api.GradleException

val releaseRequested = gradle.startParameter.taskNames.any {
    it.contains("release", ignoreCase = true)
}

if (releaseRequested) {
    fun requiredEnvironment(name: String): String =
        providers.environmentVariable(name).orNull?.trim()?.takeIf { it.isNotEmpty() }
            ?: throw GradleException("$name is required for an Android release build")

    extensions.configure<ApplicationExtension>("android") {
        val upload = signingConfigs.create("agentsAssembleUpload") {
            storeFile = file(requiredEnvironment("ANDROID_UPLOAD_KEYSTORE"))
            storePassword = requiredEnvironment("ANDROID_UPLOAD_STORE_PASSWORD")
            keyAlias = requiredEnvironment("ANDROID_UPLOAD_KEY_ALIAS")
            keyPassword = requiredEnvironment("ANDROID_UPLOAD_KEY_PASSWORD")
        }
        buildTypes.getByName("release").signingConfig = upload
    }
}
