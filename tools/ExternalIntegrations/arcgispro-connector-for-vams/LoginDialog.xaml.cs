/*
Copyright 2025 Esri

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
implied. See the License for the specific language governing
permissions and limitations under the License.
*/

using System.Windows;

namespace VamsConnector
{
    /// <summary>
    /// Interaction logic for LoginDialog.xaml
    /// </summary>
    public partial class LoginDialog : Window
    {
        public string Username { get; private set; }
        public string Password { get; private set; }
        private readonly bool _isCognitoAuth;

        public LoginDialog(string authType)
        {
            InitializeComponent();
            
            _isCognitoAuth = (authType == "Cognito");
            
            // Configure UI based on auth type
            if (_isCognitoAuth)
            {
                // Cognito: Show password field
                CredentialLabel.Text = "Password:";
                PasswordBox.Visibility = Visibility.Visible;
                TokenTextBox.Visibility = Visibility.Collapsed;
                Height = 280; // Height for password field with buttons visible
            }
            else
            {
                // External: Show JWT token field
                CredentialLabel.Text = "JWT Token:";
                PasswordBox.Visibility = Visibility.Collapsed;
                TokenTextBox.Visibility = Visibility.Visible;
                Height = 340; // Larger height for token field with buttons visible
            }
            
            // Focus on username field when dialog opens
            Loaded += (s, e) => UsernameTextBox.Focus();
        }

        private void LoginButton_Click(object sender, RoutedEventArgs e)
        {
            Username = UsernameTextBox.Text.Trim();

            if (string.IsNullOrEmpty(Username))
            {
                MessageBox.Show("Username is required.", "Validation Error",
                    MessageBoxButton.OK, MessageBoxImage.Warning);
                UsernameTextBox.Focus();
                return;
            }

            if (_isCognitoAuth)
            {
                // Get password for Cognito
                Password = PasswordBox.Password;
                
                if (string.IsNullOrEmpty(Password))
                {
                    MessageBox.Show("Password is required.", "Validation Error",
                        MessageBoxButton.OK, MessageBoxImage.Warning);
                    PasswordBox.Focus();
                    return;
                }
            }
            else
            {
                // Get JWT token for External auth
                Password = TokenTextBox.Text.Trim();
                
                if (string.IsNullOrEmpty(Password))
                {
                    MessageBox.Show("JWT Token is required.", "Validation Error",
                        MessageBoxButton.OK, MessageBoxImage.Warning);
                    TokenTextBox.Focus();
                    return;
                }
            }

            DialogResult = true;
            Close();
        }

        private void CancelButton_Click(object sender, RoutedEventArgs e)
        {
            DialogResult = false;
            Close();
        }
    }
}
