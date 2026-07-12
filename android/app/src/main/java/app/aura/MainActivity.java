package app.aura;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Build;
import android.os.Bundle;
import android.view.WindowManager;
import android.webkit.JavascriptInterface;
import android.webkit.PermissionRequest;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.EditText;

/** Aura: full-screen wrapper around the Aura web app with mic/camera/audio enabled. */
public class MainActivity extends Activity {

    private static final String PREFS = "aura";
    private static final String KEY_URL = "server_url";
    private WebView web;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // Never let the screen sleep — the assistant must keep hearing/seeing.
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);

        requestPermissions(Build.VERSION.SDK_INT >= 33
                ? new String[]{Manifest.permission.CAMERA, Manifest.permission.RECORD_AUDIO,
                               Manifest.permission.POST_NOTIFICATIONS}
                : new String[]{Manifest.permission.CAMERA, Manifest.permission.RECORD_AUDIO}, 1);

        // Keep the process alive with a visible "Aura is active" notification.
        startForegroundService(new Intent(this, KeepAliveService.class));

        web = new WebView(this);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);   // replies auto-play, zero taps

        web.setWebViewClient(new WebViewClient());
        // In-page shutdown button -> kill service + app completely.
        web.addJavascriptInterface(new Object() {
            @JavascriptInterface
            public void shutdown() {
                runOnUiThread(() -> {
                    stopService(new Intent(MainActivity.this, KeepAliveService.class));
                    finishAndRemoveTask();
                    android.os.Process.killProcess(android.os.Process.myPid());
                });
            }
        }, "AuraNative");
        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onPermissionRequest(PermissionRequest request) {
                runOnUiThread(() -> request.grant(request.getResources()));
            }
        });
        setContentView(web);

        String url = getSharedPreferences(PREFS, MODE_PRIVATE).getString(KEY_URL, null);
        if (url == null) askForServerUrl(); else web.loadUrl(url);
    }

    private void askForServerUrl() {
        EditText input = new EditText(this);
        input.setHint("https://your-server.ngrok-free.app");
        new AlertDialog.Builder(this)
                .setTitle("Aura server URL")
                .setMessage("Enter the https address of your Aura server (e.g. the ngrok URL).")
                .setView(input)
                .setCancelable(false)
                .setPositiveButton("Connect", (d, w) -> {
                    String url = input.getText().toString().trim();
                    if (!url.startsWith("http")) url = "https://" + url;
                    getSharedPreferences(PREFS, MODE_PRIVATE)
                            .edit().putString(KEY_URL, url).apply();
                    web.loadUrl(url);
                })
                .show();
    }

    @Override
    public void onBackPressed() {
        // Long-press back not available; back clears saved URL if page failed, else goes back.
        if (web.canGoBack()) web.goBack();
        else {
            getSharedPreferences(PREFS, MODE_PRIVATE).edit().remove(KEY_URL).apply();
            askForServerUrl();
        }
    }
}
