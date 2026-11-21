# Keep kotlinx.serialization protocol models
-keepattributes *Annotation*, InnerClasses
-dontnote kotlinx.serialization.AnnotationsKt
-keepclassmembers class kotlinx.serialization.json.** {
    *** Companion;
}
-keepclasseswithmembers class kotlinx.serialization.json.** {
    kotlinx.serialization.KSerializer serializer(...);
}
-keep,includedescriptorclasses class com.gcs.shimeji.model.**$$serializer { *; }
-keepclassmembers class com.gcs.shimeji.model.** {
    *** Companion;
}
