from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.http import HttpResponse
from django.contrib import messages
from .forms import RegisterForm, ProfileUpdateForm

# Login view
def login_view(request):
    """
    Handle login for both regular page loads and HTMX partial requests.

    On a normal GET: render the full login page.
    On a POST from HTMX:
      - Success → return an HX-Redirect header so HTMX does a full-page
        navigation to the dashboard (you can't use redirect() here because
        HTMX would swap the redirect response body into #login-errors).
      - Failure → return ONLY the error fragment HTML that HTMX swaps
        into #login-errors. No full page, no template extends.
    """
    # Already authenticated users have no business on the login page
    if request.user.is_authenticated:
        return redirect("/")

    if request.method == "POST":
        email = request.POST.get("email")
        password = request.POST.get("password")
        user = authenticate(request, username=email, password=password)

        print(user)

        if user is not None:
            login(request, user)
            # HTMX can't follow a normal redirect — tell it to do a
            # client-side navigation via the HX-Redirect response header.
            response = HttpResponse(status=204)  # No content needed
            response["HX-Redirect"] = "/"
            return response
        else:
            # Return ONLY the error fragment — NOT the full page.
            # HTMX will swap this into hx-target="#login-errors".
            return HttpResponse(
                '<div class="alert alert-danger">Invalid credentials. Please try again.</div>',
                status=200,
            )

    # Plain GET — render the full login page
    return render(request, "auth/login.html")

# Logout view
@login_required(login_url='accounts/login')
def logout_view(request):
    logout(request)
    return redirect("accounts/login")

# Register view
def register_view(request):
    if request.method == "POST":
        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect("home")
        else:
            return render(request, "auth/register.html", {"form": form})
    else:
        form = RegisterForm()
    return render(request, "auth/register.html", {"form": form})

# Profile view
@login_required(login_url='accounts/login')
def profile_view(request):
    if request.method == "POST":
        form = ProfileUpdateForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Profile updated successfully")
            return redirect("profile")
    else:
        form = ProfileUpdateForm(instance=request.user)
    return render(request, "accounts/profile.html", {"form": form})
